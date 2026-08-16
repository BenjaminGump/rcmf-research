from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
import ast
import hashlib
import json
import math
import re
from typing import Any

from rcmf.benchmarks.appworld.transitions import API_CALL_RE
from rcmf.training.datasets import _parse_appworld_state_text


PROCEDURE_SIGNATURE_VERSION = "canonical_appworld_procedure_signature_6f_v1"
STATE_STAGE_VERSION = "appworld_state_stage_signature_6f_v1"
OBSERVATION_SIGNATURE_VERSION = "appworld_observation_signature_6f_v1"
PROCEDURAL_LABEL_VERSION = "procedural_memory_use_label_6f_v1"

FENCED_CODE_RE = re.compile(
    r"```(?:python)?\s*(.*?)```", flags=re.IGNORECASE | re.DOTALL
)
FALLBACK_CALL_RE = re.compile(
    r"\bapis\.([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*\("
)
SENSITIVE_VALUE_RE = re.compile(
    r"(?:access[_ -]?token|password|credential|secret|bearer)\s*[:=]\s*"
    r"(?:['\"])?([^\s,'\"}\]]+)",
    flags=re.IGNORECASE,
)
ID_KEY_RE = re.compile(r"\b([A-Za-z_]\w*(?:_id|_ids))\b")

READ_PREFIXES = (
    "get_", "show_", "list_", "search_", "find_", "read_", "describe_",
    "fetch_", "check_", "retrieve_", "lookup_",
)
WRITE_PREFIXES = (
    "add_", "archive_", "cancel_", "create_", "delete_", "draft_",
    "edit_", "invite_", "like_", "mark_", "move_", "pay_", "remove_",
    "request_", "set_", "share_", "transfer_", "unlike_", "update_",
    "write_", "save_",
)
MESSAGE_PREFIXES = ("send_", "message_", "reply_", "post_")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _code_text(text: str) -> str:
    blocks = FENCED_CODE_RE.findall(str(text))
    return "\n".join(block.strip() for block in blocks if block.strip()) or str(text).strip()


def _call_name(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _target_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [name for item in node.elts for name in _target_names(item)]
    return []


def _root_name(node: ast.AST) -> str | None:
    current = node
    while isinstance(current, (ast.Subscript, ast.Attribute)):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _literal_is_from_context(value: Any, context_text: str) -> bool:
    if not isinstance(value, (str, int, float)):
        return False
    normalized = str(value).strip().lower()
    if len(normalized) < 2:
        return False
    return normalized in str(context_text).lower()


def _value_role(
    node: ast.AST,
    *,
    context_text: str,
    api_result_variables: set[str],
) -> str:
    if isinstance(node, ast.Constant):
        return "user_or_task_text" if _literal_is_from_context(node.value, context_text) else "literal"
    if isinstance(node, (ast.List, ast.Tuple, ast.Dict, ast.Set)):
        return "literal_collection"
    if isinstance(node, ast.Call):
        name = _call_name(node.func)
        return "api_result" if name.startswith("apis.") else "computed_call"
    root = _root_name(node)
    if root is not None:
        return "prior_api_result" if root in api_result_variables else "variable"
    if isinstance(node, (ast.BinOp, ast.BoolOp, ast.Compare, ast.UnaryOp)):
        return "computed_expression"
    return "expression"


def _coarse_action_type(calls: Sequence[Mapping[str, Any]], source: str) -> str:
    if any(call["app"] == "supervisor" and call["api"] == "complete_task" for call in calls):
        return "completion"
    if any(call["app"] == "api_docs" for call in calls):
        return "api_documentation"
    if any("login" in call["api"] or "authenticate" in call["api"] for call in calls):
        return "authentication"
    if any(call["api"].startswith(MESSAGE_PREFIXES) for call in calls):
        return "message_send"
    if any(call["api"].startswith(WRITE_PREFIXES) for call in calls):
        return "write_mutation"
    if calls and all(call["api"].startswith(READ_PREFIXES) for call in calls):
        return "read_query"
    if calls:
        return "api_other"
    return "python_reasoning" if source.strip() else "empty"


def canonical_procedure_signature(
    action_text: str,
    *,
    context_text: str = "",
) -> dict[str, Any]:
    """Parse an action into a value-redacted, deterministic procedure schema."""
    source = _code_text(action_text)
    parse_status = "ast"
    syntax_error: str | None = None
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        tree = None
        parse_status = "regex_fallback" if source.strip() else "empty"
        syntax_error = f"{exc.msg}@{exc.lineno}:{exc.offset}"

    calls: list[dict[str, Any]] = []
    control_flow: list[str] = []
    dataflow: list[str] = []
    variable_map: dict[str, str] = {}
    api_result_variables: set[str] = set()
    fallback_function_names: list[str] = []

    if tree is not None:
        assignment_nodes = sorted(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
            ),
            key=lambda node: (getattr(node, "lineno", 0), getattr(node, "col_offset", 0)),
        )
        for node in assignment_nodes:
            targets: list[str]
            value: ast.AST
            if isinstance(node, ast.Assign):
                targets = [name for target in node.targets for name in _target_names(target)]
                value = node.value
            else:
                targets = _target_names(node.target)
                value = node.value
            for name in targets:
                variable_map.setdefault(name, f"v{len(variable_map) + 1}")
            if isinstance(value, ast.Call) and _call_name(value.func).startswith("apis."):
                api_result_variables.update(targets)
                kind = "api_result"
            elif isinstance(value, ast.Constant):
                kind = "literal"
            elif _root_name(value) in api_result_variables:
                kind = "derived_api_result"
                api_result_variables.update(targets)
            else:
                kind = "expression"
            for name in targets:
                dataflow.append(f"{variable_map[name]}<-{kind}")

        node_type_names = (
            (ast.For, "for"), (ast.AsyncFor, "async_for"),
            (ast.While, "while"), (ast.If, "if"), (ast.Try, "try"),
            (ast.ListComp, "list_comp"), (ast.DictComp, "dict_comp"),
            (ast.SetComp, "set_comp"), (ast.GeneratorExp, "generator"),
        )
        for node in sorted(ast.walk(tree), key=lambda item: (getattr(item, "lineno", 0), getattr(item, "col_offset", 0))):
            for cls, label in node_type_names:
                if isinstance(node, cls):
                    control_flow.append(label)
                    break

        call_nodes = sorted(
            (node for node in ast.walk(tree) if isinstance(node, ast.Call)),
            key=lambda node: (getattr(node, "lineno", 0), getattr(node, "col_offset", 0)),
        )
        for position, node in enumerate(call_nodes):
            full_name = _call_name(node.func)
            fallback_function_names.append(full_name)
            parts = full_name.split(".")
            if len(parts) != 3 or parts[0] != "apis":
                continue
            app, api = parts[1], parts[2]
            assigned = next(
                (
                    variable_map[name]
                    for assignment in assignment_nodes
                    for name in (
                        [n for target in assignment.targets for n in _target_names(target)]
                        if isinstance(assignment, ast.Assign)
                        else _target_names(assignment.target)
                    )
                    if getattr(assignment, "value", None) is node and name in variable_map
                ),
                None,
            )
            keyword_names = sorted(
                keyword.arg if keyword.arg is not None else "**kwargs"
                for keyword in node.keywords
            )
            keyword_roles = {
                str(keyword.arg if keyword.arg is not None else "**kwargs"): _value_role(
                    keyword.value,
                    context_text=context_text,
                    api_result_variables=api_result_variables,
                )
                for keyword in node.keywords
            }
            calls.append(
                {
                    "position": position,
                    "app": app,
                    "api": api,
                    "name": f"{app}.{api}",
                    "keyword_names": keyword_names,
                    "keyword_roles": keyword_roles,
                    "positional_roles": [
                        _value_role(
                            value,
                            context_text=context_text,
                            api_result_variables=api_result_variables,
                        )
                        for value in node.args
                    ],
                    "assigned_to": assigned,
                }
            )
    else:
        for position, (app, api) in enumerate(FALLBACK_CALL_RE.findall(source)):
            calls.append(
                {
                    "position": position,
                    "app": app,
                    "api": api,
                    "name": f"{app}.{api}",
                    "keyword_names": [],
                    "keyword_roles": {},
                    "positional_roles": [],
                    "assigned_to": None,
                }
            )
        fallback_function_names = sorted(set(re.findall(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(", source)))

    api_sequence = [call["name"] for call in calls]
    primary = calls[0] if calls else None
    pagination = bool(
        ("for" in control_flow or "while" in control_flow)
        and ("page_index" in source or "page_token" in source or "next_page" in source)
    )
    signature: dict[str, Any] = {
        "format": PROCEDURE_SIGNATURE_VERSION,
        "parse_status": parse_status,
        "syntax_error_category": syntax_error,
        "ordered_api_sequence": api_sequence,
        "primary_app": primary["app"] if primary else "__no_api__",
        "primary_api": primary["name"] if primary else "__no_api__",
        "all_app_api_pairs": sorted(set(api_sequence)),
        "coarse_action_type": _coarse_action_type(calls, source),
        "api_documentation_action": any(call["app"] == "api_docs" for call in calls),
        "authentication_login_action": any("login" in call["api"] or "authenticate" in call["api"] for call in calls),
        "read_query_action": bool(calls) and all(call["api"].startswith(READ_PREFIXES) for call in calls),
        "write_mutation_action": any(call["api"].startswith(WRITE_PREFIXES) for call in calls),
        "message_send_action": any(call["api"].startswith(MESSAGE_PREFIXES) for call in calls),
        "completion_action": any(call["app"] == "supervisor" and call["api"] == "complete_task" for call in calls),
        "python_only_reasoning_action": not calls and bool(source.strip()),
        "calls": calls,
        "keyword_argument_names": sorted({name for call in calls for name in call["keyword_names"]}),
        "argument_value_source_roles": sorted({role for call in calls for role in [*call["keyword_roles"].values(), *call["positional_roles"]]}),
        "control_flow_constructs": control_flow,
        "pagination_loop_pattern": pagination,
        "conditional_pattern": "if" in control_flow,
        "function_ast_call_names": sorted(set(fallback_function_names)),
        "assignment_dataflow_pattern": dataflow,
        "contains_sensitive_literal": bool(SENSITIVE_VALUE_RE.search(source)),
    }
    signature["signature_sha256"] = stable_hash(signature)
    return signature


def observation_signature(text: str) -> dict[str, Any]:
    raw = str(text).strip()
    lowered = raw.lower()
    if not raw or raw in {"[]", "{}", "None", "null"}:
        category = "empty"
    elif any(value in lowered for value in ("traceback", "exception", "error:", "execution failed")):
        category = "error"
    elif "execution successful" in lowered:
        category = "success"
    elif raw.startswith("["):
        category = "list"
    elif raw.startswith("{") or "{" in raw:
        category = "mapping"
    else:
        category = "text"
    keys = sorted(set(re.findall(r"['\"]([A-Za-z_]\w*)['\"]\s*:", raw)))
    signature = {
        "format": OBSERVATION_SIGNATURE_VERSION,
        "category": category,
        "schema_keys": keys,
        "id_keys": sorted(set(ID_KEY_RE.findall(raw))),
        "has_access_token_key": "access_token" in keys or "access_token" in lowered,
        "is_error": category == "error",
        "is_empty": category == "empty",
    }
    signature["signature_sha256"] = stable_hash(signature)
    return signature


def state_stage_signature(state_text: str) -> dict[str, Any]:
    """Derive deployment-time stage only from current task history."""
    _, _, steps = _parse_appworld_state_text(str(state_text))
    responses = [response for _, response, _ in steps]
    observations = [observation for _, _, observation in steps]
    history = "\n".join([*responses, *observations])
    calls = [
        name
        for response in responses
        for name in canonical_procedure_signature(response)["ordered_api_sequence"]
    ]
    obs_signatures = [observation_signature(value) for value in observations]
    last_observation = obs_signatures[-1] if obs_signatures else observation_signature("")
    credentials = any(name == "supervisor.show_account_passwords" for name in calls) or "password" in history.lower()
    authenticated = any(name.endswith(".login") or name.endswith(".authenticate") for name in calls) and not last_observation["is_error"]
    token_state = any(value["has_access_token_key"] for value in obs_signatures) or "access_token" in "\n".join(responses)
    ids = sorted({key for value in obs_signatures for key in value["id_keys"]})
    collection_loaded = any(
        name.split(".")[-1].startswith(("list_", "search_", "get_all", "show_all"))
        for name in calls
    ) or any(value["category"] == "list" for value in obs_signatures)
    pagination = "page_index" in "\n".join(responses) or "page_token" in "\n".join(responses)
    meaningful = [name for name in calls if not name.startswith("api_docs.") and not name.endswith(".login")]
    signature = {
        "format": STATE_STAGE_VERSION,
        "history_step_count": len(steps),
        "pre_action_api_history": calls,
        "docs_known": any(name.startswith("api_docs.") for name in calls),
        "credentials_obtained": credentials,
        "authenticated": authenticated,
        "authentication_token_present": token_state,
        "object_ids_available": bool(ids),
        "available_id_keys": ids,
        "collection_loaded": collection_loaded,
        "pagination_state": pagination,
        "completion_ready": bool(meaningful) and not last_observation["is_error"],
        "latest_observation_category": last_observation["category"],
        "latest_observation_schema_keys": last_observation["schema_keys"],
        "future_target_action_accessed": False,
        "future_observation_accessed": False,
    }
    signature["signature_sha256"] = stable_hash(signature)
    return signature


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    a, b = set(left), set(right)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if a or b else 1.0


def _stage_compatibility(query: Mapping[str, Any], transition: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "docs_known", "credentials_obtained", "authenticated",
        "authentication_token_present", "object_ids_available",
        "collection_loaded", "pagination_state", "completion_ready",
    )
    matches = {field: bool(query[field]) == bool(transition[field]) for field in fields}
    score = sum(matches.values()) / len(fields)
    return {
        "score": score,
        "compatible": score >= 0.75,
        "conflict_count": len(fields) - sum(matches.values()),
        "matches": matches,
    }


def procedural_compatibility(
    query_action: Mapping[str, Any],
    query_stage: Mapping[str, Any],
    transition_action: Mapping[str, Any],
    transition_stage: Mapping[str, Any],
    transition_observation: Mapping[str, Any],
) -> dict[str, Any]:
    same_coarse = query_action["coarse_action_type"] == transition_action["coarse_action_type"]
    same_app = query_action["primary_app"] == transition_action["primary_app"] and query_action["primary_app"] != "__no_api__"
    exact_sequence = query_action["ordered_api_sequence"] == transition_action["ordered_api_sequence"]
    keyword_similarity = _jaccard(query_action["keyword_argument_names"], transition_action["keyword_argument_names"])
    role_similarity = _jaccard(query_action["argument_value_source_roles"], transition_action["argument_value_source_roles"])
    control_similarity = _jaccard(query_action["control_flow_constructs"], transition_action["control_flow_constructs"])
    dataflow_similarity = _jaccard(query_action["assignment_dataflow_pattern"], transition_action["assignment_dataflow_pattern"])
    argument_control_compatible = keyword_similarity >= 0.5 and control_similarity >= 0.5
    canonical_schema = (
        exact_sequence
        and keyword_similarity == 1.0
        and role_similarity == 1.0
        and control_similarity == 1.0
        and dataflow_similarity == 1.0
    )
    stage = _stage_compatibility(query_stage, transition_stage)
    if canonical_schema and stage["compatible"]:
        tier = 4
    elif exact_sequence and argument_control_compatible:
        tier = 3
    elif same_app and same_coarse:
        tier = 2
    elif same_coarse:
        tier = 1
    else:
        tier = 0
    if tier > 0 and not stage["compatible"]:
        tier -= 1

    coarse = query_action["coarse_action_type"]
    expected_categories = {
        "api_documentation": {"mapping", "list", "text"},
        "read_query": {"mapping", "list", "text"},
        "authentication": {"mapping", "success"},
        "write_mutation": {"mapping", "success"},
        "message_send": {"mapping", "success"},
        "completion": {"success", "text"},
        "python_reasoning": {"mapping", "list", "text", "success"},
        "api_other": {"mapping", "list", "text", "success"},
    }.get(str(coarse), {"mapping", "list", "text", "success"})
    observation_compatible = transition_observation["category"] in expected_categories
    return {
        "format": PROCEDURAL_LABEL_VERSION,
        "tier": tier,
        "P0_coarse_intent_match": float(same_app and same_coarse),
        "P1_exact_api_compatibility": float(exact_sequence),
        "P2_canonical_schema_compatibility": float((keyword_similarity + role_similarity + control_similarity + dataflow_similarity) / 4.0),
        "P3_state_stage_compatibility": float(stage["score"]),
        "P4_observation_schema_compatibility": float(observation_compatible),
        "same_primary_app": same_app,
        "same_coarse_action_type": same_coarse,
        "exact_api_sequence": exact_sequence,
        "canonical_action_schema_match": canonical_schema,
        "argument_control_compatible": argument_control_compatible,
        "keyword_similarity": keyword_similarity,
        "argument_role_similarity": role_similarity,
        "control_flow_similarity": control_similarity,
        "dataflow_similarity": dataflow_similarity,
        "state_stage_conflict_count": stage["conflict_count"],
        "state_stage_compatible": stage["compatible"],
        "observation_schema_compatible": observation_compatible,
    }


def summarize_label_coverage(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cells: dict[str, Any] = {}
    for cell in "ABCD":
        selected = [row for row in rows if str(row["cell"]) == cell]
        grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in selected:
            grouped[str(row["state_example_id"])].append(row)
        high_states = sum(any(int(row["procedural_tier"]) >= 3 for row in values) for values in grouped.values())
        exact_states = sum(any(bool(row["exact_api_sequence"]) for row in values) for values in grouped.values())
        cells[cell] = {
            "row_count": len(selected),
            "state_count": len(grouped),
            "tier_counts": dict(Counter(int(row["procedural_tier"]) for row in selected)),
            "states_with_tier3_or_4": high_states,
            "tier3_or_4_state_coverage": high_states / len(grouped) if grouped else 0.0,
            "states_with_exact_api": exact_states,
            "exact_api_state_coverage": exact_states / len(grouped) if grouped else 0.0,
            "task_count": len({str(row["state_task_id"]) for row in selected}),
            "parent_count": len({str(row["transition_parent_id"]) for row in selected}),
        }
    return {"format": PROCEDURAL_LABEL_VERSION, "cells": cells}

