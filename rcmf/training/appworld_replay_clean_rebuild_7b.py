from __future__ import annotations

import ast
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SEMANTIC_NORMALIZATION_VERSION = "appworld_observation_semantic_normalization_7b_v1"
SEMANTIC_REPLAY_CONTRACT_VERSION = "appworld_semantic_replay_contract_7b_v1"
SEMANTIC_REPLAY_RESULT_VERSION = "appworld_semantic_replay_result_7b_v1"
ALLOWED_TOKEN_FIELDS = frozenset({"access_token"})
ALLOWED_TEMPORAL_CLAIMS = frozenset({"exp"})
ALLOWED_ROOT_JWT_PATHS = frozenset({"$"})
ROOT_JWT_SCHEMA_SENTINEL_IDS = (
    "appworld:trace:82e2fac_3:step:6:line:16",
    "appworld:trace:82e2fac_3:step:7:line:17",
    "appworld:trace:82e2fac_3:step:10:line:20",
)


def _load_locked_v2() -> Any:
    path = Path(__file__).with_name("appworld_semantic_replay_6h2.py")
    name = "_rcmf_appworld_semantic_replay_6h2_locked_for_7b"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load locked semantic-v2 module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_V2 = _load_locked_v2()
canonical_hash = _V2.canonical_hash
decode_jwt_strict = _V2.decode_jwt_strict
identity_hashes = _V2.identity_hashes
normalize_observation_locked = _V2.normalize_observation_locked
compare_observations_semantic_v2 = _V2.compare_observations_semantic


@dataclass(frozen=True)
class LoginActionContext:
    parse_ok: bool
    login_call_count: int
    appworld_call_count: int
    app_name: str | None
    assigned_names: tuple[str, ...]
    error: str | None = None


def _appworld_call_signature(call: ast.Call) -> tuple[str, str] | None:
    func = call.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Attribute):
        return None
    app_node = func.value
    if not isinstance(app_node.value, ast.Name) or app_node.value.id != "apis":
        return None
    return app_node.attr, func.attr


def _assigned_names_for_call(tree: ast.AST, target_call: ast.Call) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        value: ast.AST | None = None
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            value = node.value
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            value = node.value
            targets = [node.target]
        elif isinstance(node, ast.NamedExpr):
            value = node.value
            targets = [node.target]
        if value is None or target_call not in set(ast.walk(value)):
            continue
        for target in targets:
            names.update(
                child.id for child in ast.walk(target) if isinstance(child, ast.Name)
            )
    return names


def analyze_login_action(code: str) -> LoginActionContext:
    try:
        tree = ast.parse(str(code))
    except SyntaxError as error:
        return LoginActionContext(
            parse_ok=False,
            login_call_count=0,
            appworld_call_count=0,
            app_name=None,
            assigned_names=(),
            error=type(error).__name__,
        )
    appworld_calls = [
        (node, signature)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (signature := _appworld_call_signature(node)) is not None
    ]
    login_calls = [
        (node, signature)
        for node, signature in appworld_calls
        if signature[1] == "login"
    ]
    if len(login_calls) != 1:
        return LoginActionContext(
            parse_ok=True,
            login_call_count=len(login_calls),
            appworld_call_count=len(appworld_calls),
            app_name=None,
            assigned_names=(),
        )
    call, signature = login_calls[0]
    return LoginActionContext(
        parse_ok=True,
        login_call_count=1,
        appworld_call_count=len(appworld_calls),
        app_name=signature[0],
        assigned_names=tuple(sorted(_assigned_names_for_call(tree, call))),
    )


def authenticated_calls_using_login_result(
    code: str,
    *,
    app_name: str,
    assigned_names: Sequence[str],
) -> list[dict[str, Any]]:
    if not assigned_names:
        return []
    try:
        tree = ast.parse(str(code))
    except SyntaxError:
        return []
    allowed_names = set(assigned_names)
    reports: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        signature = _appworld_call_signature(node)
        if signature is None or signature[0] != app_name or signature[1] == "login":
            continue
        token_keywords = [keyword for keyword in node.keywords if keyword.arg == "access_token"]
        if not token_keywords:
            continue
        referenced = sorted(
            {
                child.id
                for keyword in token_keywords
                for child in ast.walk(keyword.value)
                if isinstance(child, ast.Name) and child.id in allowed_names
            }
        )
        if referenced:
            reports.append(
                {
                    "app": signature[0],
                    "api": signature[1],
                    "referenced_login_result_names": referenced,
                }
            )
    return reports


def _parse_root_value(value: str) -> Any:
    normalized = normalize_observation_locked(value)
    try:
        return json.loads(normalized)
    except Exception:  # noqa: BLE001 - locked v1 deliberately permits plain text
        try:
            return ast.literal_eval(normalized)
        except Exception:  # noqa: BLE001 - preserve a plain root observation
            return normalized


def _root_jwt_pair_report(expected: str, actual: str) -> dict[str, Any]:
    wrapped = _V2.compare_observations_semantic(
        json.dumps({"access_token": expected}),
        json.dumps({"access_token": actual}),
    )
    report = dict(wrapped["jwt_reports"][0]) if wrapped["jwt_reports"] else {}
    if report:
        report["path"] = "$"
    return report


def compare_observations_semantic_v3(
    expected: str,
    actual: str,
    *,
    action_code: str,
    expected_validator_accepted: bool = False,
    actual_validator_accepted: bool = False,
    subsequent_authenticated_action_count: int = 0,
    subsequent_authenticated_actions_accepted: bool = True,
) -> dict[str, Any]:
    v2 = _V2.compare_observations_semantic(expected, actual)
    context = analyze_login_action(action_code)
    expected_root = _parse_root_value(expected)
    actual_root = _parse_root_value(actual)
    root_strings = isinstance(expected_root, str) and isinstance(actual_root, str)
    root_report: dict[str, Any] | None = None
    valid_root_pair = False
    if root_strings:
        try:
            decode_jwt_strict(expected_root)
            decode_jwt_strict(actual_root)
            valid_root_pair = True
            root_report = _root_jwt_pair_report(expected_root, actual_root)
        except ValueError:
            valid_root_pair = False

    requirements = {
        "allowed_root_path": "$" in ALLOWED_ROOT_JWT_PATHS,
        "action_parsed": context.parse_ok,
        "exactly_one_login_call": context.login_call_count == 1,
        "complete_observations_are_root_strings": root_strings,
        "both_roots_are_valid_jwts": valid_root_pair,
        "expected_validator_accepted": bool(expected_validator_accepted),
        "actual_validator_accepted": bool(actual_validator_accepted),
        "header_match": bool(root_report and root_report.get("header_match")),
        "stable_claims_match": bool(
            root_report and root_report.get("stable_claims_match")
        ),
        "temporal_claim_presence_match": bool(
            root_report and root_report.get("temporal_claim_presence_match")
        ),
        "only_exp_differs": bool(
            root_report
            and root_report.get("differing_claims") == ["exp"]
            and not root_report.get("non_temporal_differing_claims")
        ),
        "subsequent_authenticated_actions_accepted": bool(
            subsequent_authenticated_actions_accepted
        ),
        "no_additional_response_content": root_strings,
    }
    root_extension_match = all(requirements.values())
    semantic_v3_match = bool(v2["semantic_v2_match"] or root_extension_match)

    semantic_identity = None
    if root_extension_match:
        decoded = decode_jwt_strict(expected_root)
        stable_claims = {
            key: value
            for key, value in decoded.payload.items()
            if key not in ALLOWED_TEMPORAL_CLAIMS
        }
        semantic_identity = {
            "kind": "semantic_root_login_jwt",
            "path": "$",
            "header": decoded.header,
            "stable_claims": stable_claims,
            "temporal_claims_present": sorted(
                set(decoded.payload) & set(ALLOWED_TEMPORAL_CLAIMS)
            ),
        }
        semantic_identity["semantic_identity_sha256"] = canonical_hash(
            semantic_identity
        )

    expected_v3_sha256 = (
        str(semantic_identity["semantic_identity_sha256"])
        if semantic_identity is not None
        else str(v2["expected_semantic_sha256"])
    )
    actual_v3_sha256 = (
        str(semantic_identity["semantic_identity_sha256"])
        if semantic_identity is not None
        else str(v2["actual_semantic_sha256"])
    )

    return {
        "format": SEMANTIC_NORMALIZATION_VERSION,
        "raw_match": bool(v2["raw_match"]),
        "v1_match": bool(v2["v1_match"]),
        "semantic_v2_match": bool(v2["semantic_v2_match"]),
        "semantic_v3_match": semantic_v3_match,
        "expected_semantic_v3_sha256": expected_v3_sha256,
        "actual_semantic_v3_sha256": actual_v3_sha256,
        "locked_v2": v2,
        "root_login_context": asdict(context),
        "root_jwt_candidate": bool(root_strings and valid_root_pair),
        "root_jwt_extension_applied": bool(
            root_extension_match and not v2["semantic_v2_match"]
        ),
        "root_jwt_requirements": requirements,
        "root_jwt_report": root_report,
        "root_jwt_semantic_identity": semantic_identity,
        "subsequent_authenticated_action_count": int(
            subsequent_authenticated_action_count
        ),
        "allowed_root_jwt_paths": sorted(ALLOWED_ROOT_JWT_PATHS),
        "allowed_token_fields": sorted(ALLOWED_TOKEN_FIELDS),
        "allowed_temporal_claims": sorted(ALLOWED_TEMPORAL_CLAIMS),
    }


def summarize_semantic_replays_v3(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not rows:
        raise ValueError("Semantic-v3 replay summary requires rows")
    state_ids = [str(row["state_example_id"]) for row in rows]
    if len(state_ids) != len(set(state_ids)):
        raise ValueError("Semantic-v3 replay rows contain duplicate state keys")
    steps = [step for row in rows for step in row.get("steps", [])]
    history = [step for step in steps if not bool(step["is_target"])]
    targets = [step for step in steps if bool(step["is_target"])]
    root_reports = [
        step["semantic_comparison"].get("root_jwt_report")
        for step in steps
        if step.get("semantic_comparison", {}).get("root_jwt_report") is not None
    ]
    by_task: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_task[str(row["task_id"])].append(row)
    first_divergences = Counter(
        "none"
        if row.get("first_semantic_v3_divergence_step") is None
        else str(row["first_semantic_v3_divergence_step"])
        for row in rows
    )
    return {
        "format": "appworld_semantic_replay_summary_7b_v1",
        "state_count": len(rows),
        "task_count": len(by_task),
        "identity_match_count": sum(
            bool(row["initial_task_identity_match"]) for row in rows
        ),
        "complete_history_raw_match_count": sum(
            bool(row["complete_history_raw_match"]) for row in rows
        ),
        "complete_history_v1_match_count": sum(
            bool(row["complete_history_v1_match"]) for row in rows
        ),
        "complete_history_v2_match_count": sum(
            bool(row["complete_history_v2_match"]) for row in rows
        ),
        "complete_history_v3_match_count": sum(
            bool(row["complete_history_v3_match"]) for row in rows
        ),
        "prior_observation_count": len(history),
        "prior_raw_match_count": sum(bool(step["raw_match"]) for step in history),
        "prior_v1_match_count": sum(bool(step["v1_match"]) for step in history),
        "prior_v2_match_count": sum(
            bool(step["semantic_v2_match"]) for step in history
        ),
        "prior_v3_match_count": sum(
            bool(step["semantic_v3_match"]) for step in history
        ),
        "target_observation_count": len(targets),
        "target_raw_match_count": sum(bool(step["raw_match"]) for step in targets),
        "target_v1_match_count": sum(bool(step["v1_match"]) for step in targets),
        "target_v2_match_count": sum(
            bool(step["semantic_v2_match"]) for step in targets
        ),
        "target_v3_match_count": sum(
            bool(step["semantic_v3_match"]) for step in targets
        ),
        "complete_v3_replay_count": sum(bool(row["passed"]) for row in rows),
        "exception_count": sum(row.get("fatal_exception") is not None for row in rows),
        "root_jwt_pair_count": len(root_reports),
        "root_jwt_extension_count": sum(
            bool(step.get("semantic_comparison", {}).get("root_jwt_extension_applied"))
            for step in steps
        ),
        "temporal_only_root_jwt_count": sum(
            bool(report)
            and report.get("differing_claims") == ["exp"]
            and not bool(report.get("non_temporal_differing_claims"))
            for report in root_reports
        ),
        "non_temporal_root_jwt_mismatch_count": sum(
            bool(report.get("non_temporal_differing_claims"))
            or not bool(report.get("header_match", False))
            or not bool(report.get("stable_claims_match", False))
            for report in root_reports
        ),
        "first_semantic_v3_divergence_distribution": dict(
            sorted(first_divergences.items())
        ),
        "per_task": {
            task_id: {
                "state_count": len(task_rows),
                "complete_v3_replay_count": sum(
                    bool(row["passed"]) for row in task_rows
                ),
            }
            for task_id, task_rows in sorted(by_task.items())
        },
    }


def semantic_replay_gate_v3(
    repeat_summaries: Sequence[Mapping[str, Any]],
    *,
    expected_states: int,
    expected_tasks: int,
    expected_prior_observations: int,
) -> dict[str, Any]:
    checks = []
    for summary in repeat_summaries:
        checks.append(
            {
                "state_count": int(summary["state_count"]) == expected_states,
                "task_count": int(summary["task_count"]) == expected_tasks,
                "identity": int(summary["identity_match_count"]) == expected_states,
                "history": int(summary["complete_history_v3_match_count"])
                == expected_states,
                "prior_count": int(summary["prior_observation_count"])
                == expected_prior_observations,
                "prior_match": int(summary["prior_v3_match_count"])
                == expected_prior_observations,
                "target_count": int(summary["target_observation_count"])
                == expected_states,
                "target_match": int(summary["target_v3_match_count"])
                == expected_states,
                "complete": int(summary["complete_v3_replay_count"])
                == expected_states,
                "exceptions": int(summary["exception_count"]) == 0,
                "non_temporal_root_jwt_mismatches": int(
                    summary["non_temporal_root_jwt_mismatch_count"]
                )
                == 0,
            }
        )
    return {
        "expected_states": expected_states,
        "expected_tasks": expected_tasks,
        "expected_prior_observations": expected_prior_observations,
        "repeat_checks": checks,
        "passed": bool(checks and all(all(row.values()) for row in checks)),
    }
