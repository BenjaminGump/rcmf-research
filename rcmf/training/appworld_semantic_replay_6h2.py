from __future__ import annotations

import ast
import base64
import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

SEMANTIC_NORMALIZATION_VERSION = "appworld_observation_semantic_normalization_6h2_v1"
SEMANTIC_REPLAY_CONTRACT_VERSION = "appworld_semantic_replay_contract_6h2_v1"
SEMANTIC_REPLAY_RESULT_VERSION = "appworld_semantic_replay_result_6h2_v1"
ALLOWED_TOKEN_FIELDS = frozenset({"access_token"})
ALLOWED_TEMPORAL_CLAIMS = frozenset({"exp"})

_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_FULL_DEMO_QUERY_RE = re.compile(
    r"^Now here is another task in a different environment\. The task is the following:\n"
    r"My name is: (?P<first_name>.*?) (?P<last_name>.*?)\. "
    r"My personal email is (?P<email>.*?) and phone number is "
    r"(?P<phone_number>.*?)\.\nTask: (?P<instruction>.*)$",
    flags=re.DOTALL,
)


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def value_hash(value: Any) -> str:
    return canonical_hash({"type": type(value).__name__, "value": value})


def normalize_observation_locked(text: str) -> str:
    """Byte-for-byte behavior of appworld_observation_normalization_6h_v1."""
    value = str(text).replace("\r\n", "\n").strip()
    if value.startswith("Output:\n```") and value.endswith("```"):
        value = value[len("Output:\n```") : -3].strip()
    value = "\n".join(line.rstrip() for line in value.splitlines()).strip()
    parsed: Any = None
    parsed_ok = False
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(value)
            parsed_ok = True
            break
        except Exception:  # noqa: BLE001,S112 - locked EXP-024A behavior
            continue
    if parsed_ok:
        try:
            return json.dumps(
                parsed,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                default=str,
            )
        except Exception:  # noqa: BLE001,S110 - locked EXP-024A behavior
            pass
    return value


def _decode_base64url_json(segment: str) -> Any:
    if not segment or _BASE64URL_RE.fullmatch(segment) is None:
        raise ValueError("JWT segment is not canonical base64url text")
    padded = segment + "=" * (-len(segment) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        return json.loads(decoded.decode("utf-8"))
    except Exception as error:  # noqa: BLE001 - converted to a strict audit failure
        raise ValueError("JWT segment is not decodable JSON") from error


@dataclass(frozen=True)
class DecodedJWT:
    header: dict[str, Any]
    payload: dict[str, Any]
    signature_sha256: str
    token_sha256: str


def decode_jwt_strict(token: str) -> DecodedJWT:
    parts = str(token).split(".")
    if len(parts) != 3:
        raise ValueError("JWT must contain exactly three segments")
    header = _decode_base64url_json(parts[0])
    payload = _decode_base64url_json(parts[1])
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise ValueError("JWT header and payload must be JSON objects")
    if not parts[2] or _BASE64URL_RE.fullmatch(parts[2]) is None:
        raise ValueError("JWT signature is not canonical base64url text")
    return DecodedJWT(
        header=dict(header),
        payload=dict(payload),
        signature_sha256=hashlib.sha256(parts[2].encode("ascii")).hexdigest(),
        token_sha256=hashlib.sha256(str(token).encode("utf-8")).hexdigest(),
    )


def _semantic_jwt(
    token: DecodedJWT,
    *,
    allowed_temporal_claims: frozenset[str],
) -> dict[str, Any]:
    stable_claims = {
        key: value
        for key, value in token.payload.items()
        if key not in allowed_temporal_claims
    }
    temporal_claims = sorted(set(token.payload) & set(allowed_temporal_claims))
    identity = {
        "header": token.header,
        "stable_claims": stable_claims,
        "temporal_claims_present": temporal_claims,
    }
    return {
        "kind": "semantic_jwt",
        **identity,
        "semantic_identity_sha256": canonical_hash(identity),
    }


def _jwt_pair_report(
    expected: str,
    actual: str,
    *,
    path: str,
    allowed_temporal_claims: frozenset[str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        expected_token = decode_jwt_strict(expected)
        actual_token = decode_jwt_strict(actual)
    except ValueError as error:
        report = {
            "path": path,
            "valid_jwt_pair": False,
            "semantic_match": False,
            "reason": str(error),
            "expected_value_sha256": hashlib.sha256(str(expected).encode()).hexdigest(),
            "actual_value_sha256": hashlib.sha256(str(actual).encode()).hexdigest(),
        }
        return (
            {"kind": "invalid_semantic_jwt", "value_sha256": report["expected_value_sha256"]},
            {"kind": "invalid_semantic_jwt", "value_sha256": report["actual_value_sha256"]},
            report,
        )

    expected_semantic = _semantic_jwt(
        expected_token, allowed_temporal_claims=allowed_temporal_claims
    )
    actual_semantic = _semantic_jwt(
        actual_token, allowed_temporal_claims=allowed_temporal_claims
    )
    header_match = expected_token.header == actual_token.header
    stable_match = (
        expected_semantic["stable_claims"] == actual_semantic["stable_claims"]
    )
    temporal_presence_match = (
        expected_semantic["temporal_claims_present"]
        == actual_semantic["temporal_claims_present"]
    )
    differing_claims = sorted(
        key
        for key in set(expected_token.payload) | set(actual_token.payload)
        if expected_token.payload.get(key) != actual_token.payload.get(key)
    )
    non_temporal_differences = sorted(
        set(differing_claims) - set(allowed_temporal_claims)
    )
    temporal_deltas: dict[str, float | None] = {}
    for claim in sorted(allowed_temporal_claims):
        left = expected_token.payload.get(claim)
        right = actual_token.payload.get(claim)
        temporal_deltas[claim] = (
            float(right) - float(left)
            if isinstance(left, (int, float)) and isinstance(right, (int, float))
            else None
        )
    semantic_match = bool(
        header_match
        and stable_match
        and temporal_presence_match
        and not non_temporal_differences
    )
    report = {
        "path": path,
        "valid_jwt_pair": True,
        "semantic_match": semantic_match,
        "expected_token_sha256": expected_token.token_sha256,
        "actual_token_sha256": actual_token.token_sha256,
        "expected_signature_sha256": expected_token.signature_sha256,
        "actual_signature_sha256": actual_token.signature_sha256,
        "header": {
            "algorithm": expected_token.header.get("alg"),
            "type": expected_token.header.get("typ"),
            "header_sha256": canonical_hash(expected_token.header),
        },
        "header_match": header_match,
        "claim_names": sorted(set(expected_token.payload) | set(actual_token.payload)),
        "differing_claims": differing_claims,
        "non_temporal_differing_claims": non_temporal_differences,
        "stable_claims_match": stable_match,
        "stable_claims_sha256": canonical_hash(expected_semantic["stable_claims"]),
        "temporal_claims_present": expected_semantic["temporal_claims_present"],
        "temporal_claim_presence_match": temporal_presence_match,
        "temporal_claim_deltas": temporal_deltas,
        "semantic_identity_sha256": expected_semantic["semantic_identity_sha256"],
    }
    return expected_semantic, actual_semantic, report


def _compare_values(
    expected: Any,
    actual: Any,
    *,
    path: str,
    field_name: str | None,
    allowed_token_fields: frozenset[str],
    allowed_temporal_claims: frozenset[str],
    jwt_reports: list[dict[str, Any]],
    non_token_differences: list[dict[str, Any]],
) -> tuple[Any, Any]:
    if field_name in allowed_token_fields:
        if not isinstance(expected, str) or not isinstance(actual, str):
            non_token_differences.append(
                {
                    "path": path,
                    "reason": "allowed_token_field_is_not_string_pair",
                    "expected_sha256": value_hash(expected),
                    "actual_sha256": value_hash(actual),
                }
            )
            return expected, actual
        left, right, report = _jwt_pair_report(
            expected,
            actual,
            path=path,
            allowed_temporal_claims=allowed_temporal_claims,
        )
        jwt_reports.append(report)
        return left, right

    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        if set(expected) != set(actual):
            non_token_differences.append(
                {
                    "path": path,
                    "reason": "mapping_keys_differ",
                    "expected_keys": sorted(str(key) for key in expected),
                    "actual_keys": sorted(str(key) for key in actual),
                }
            )
        left: dict[str, Any] = {}
        right: dict[str, Any] = {}
        for key in sorted(set(expected) | set(actual), key=str):
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if key not in expected or key not in actual:
                left[key_text] = expected.get(key)
                right[key_text] = actual.get(key)
                continue
            child_left, child_right = _compare_values(
                expected[key],
                actual[key],
                path=child_path,
                field_name=key_text,
                allowed_token_fields=allowed_token_fields,
                allowed_temporal_claims=allowed_temporal_claims,
                jwt_reports=jwt_reports,
                non_token_differences=non_token_differences,
            )
            left[key_text] = child_left
            right[key_text] = child_right
        return left, right

    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            non_token_differences.append(
                {
                    "path": path,
                    "reason": "list_lengths_differ",
                    "expected_length": len(expected),
                    "actual_length": len(actual),
                }
            )
        left_items: list[Any] = []
        right_items: list[Any] = []
        for index in range(max(len(expected), len(actual))):
            if index >= len(expected) or index >= len(actual):
                left_items.append(expected[index] if index < len(expected) else None)
                right_items.append(actual[index] if index < len(actual) else None)
                continue
            child_left, child_right = _compare_values(
                expected[index],
                actual[index],
                path=f"{path}[{index}]",
                field_name=None,
                allowed_token_fields=allowed_token_fields,
                allowed_temporal_claims=allowed_temporal_claims,
                jwt_reports=jwt_reports,
                non_token_differences=non_token_differences,
            )
            left_items.append(child_left)
            right_items.append(child_right)
        return left_items, right_items

    if expected != actual:
        non_token_differences.append(
            {
                "path": path,
                "reason": "non_token_value_differs",
                "expected_type": type(expected).__name__,
                "actual_type": type(actual).__name__,
                "expected_sha256": value_hash(expected),
                "actual_sha256": value_hash(actual),
            }
        )
    return expected, actual


def _parse_v1_value(normalized: str) -> Any:
    try:
        return json.loads(normalized)
    except Exception:  # noqa: BLE001 - v1 may intentionally remain plain text
        try:
            return ast.literal_eval(normalized)
        except Exception:  # noqa: BLE001 - preserve plain-text observation
            return normalized


def compare_observations_semantic(
    expected: str,
    actual: str,
    *,
    allowed_token_fields: frozenset[str] = ALLOWED_TOKEN_FIELDS,
    allowed_temporal_claims: frozenset[str] = ALLOWED_TEMPORAL_CLAIMS,
) -> dict[str, Any]:
    expected_v1 = normalize_observation_locked(expected)
    actual_v1 = normalize_observation_locked(actual)
    jwt_reports: list[dict[str, Any]] = []
    non_token_differences: list[dict[str, Any]] = []
    expected_semantic, actual_semantic = _compare_values(
        _parse_v1_value(expected_v1),
        _parse_v1_value(actual_v1),
        path="$",
        field_name=None,
        allowed_token_fields=allowed_token_fields,
        allowed_temporal_claims=allowed_temporal_claims,
        jwt_reports=jwt_reports,
        non_token_differences=non_token_differences,
    )
    expected_semantic_sha = canonical_hash(expected_semantic)
    actual_semantic_sha = canonical_hash(actual_semantic)
    semantic_match = bool(
        expected_semantic == actual_semantic
        and all(report["semantic_match"] for report in jwt_reports)
        and not non_token_differences
    )
    return {
        "format": SEMANTIC_NORMALIZATION_VERSION,
        "raw_match": str(expected) == str(actual),
        "v1_match": expected_v1 == actual_v1,
        "semantic_v2_match": semantic_match,
        "expected_raw_sha256": hashlib.sha256(str(expected).encode()).hexdigest(),
        "actual_raw_sha256": hashlib.sha256(str(actual).encode()).hexdigest(),
        "expected_v1_sha256": hashlib.sha256(expected_v1.encode()).hexdigest(),
        "actual_v1_sha256": hashlib.sha256(actual_v1.encode()).hexdigest(),
        "expected_semantic_sha256": expected_semantic_sha,
        "actual_semantic_sha256": actual_semantic_sha,
        "jwt_field_count": len(jwt_reports),
        "jwt_reports": jwt_reports,
        "non_token_difference_count": len(non_token_differences),
        "non_token_differences": non_token_differences,
        "allowed_token_fields": sorted(allowed_token_fields),
        "allowed_temporal_claims": sorted(allowed_temporal_claims),
    }


def parse_full_demo_query(query: str) -> dict[str, str]:
    match = _FULL_DEMO_QUERY_RE.fullmatch(str(query).strip())
    if match is None:
        raise ValueError("Source query does not match the locked full-demo task format")
    return {key: value for key, value in match.groupdict().items()}


def identity_hashes(query: str) -> dict[str, str]:
    parsed = parse_full_demo_query(query)
    return {
        key: hashlib.sha256(value.encode("utf-8")).hexdigest()
        for key, value in sorted(parsed.items())
    }


def compare_identity_layers(
    source_queries: Mapping[str, str],
    *,
    official_fields: Mapping[str, str],
) -> dict[str, Any]:
    if not source_queries:
        raise ValueError("Identity audit requires source-query layers")
    parsed_layers = {
        name: parse_full_demo_query(value) for name, value in source_queries.items()
    }
    layer_hashes = {
        name: {
            key: hashlib.sha256(value.encode("utf-8")).hexdigest()
            for key, value in sorted(fields.items())
        }
        for name, fields in sorted(parsed_layers.items())
    }
    official_hashes = {
        key: hashlib.sha256(str(value).encode("utf-8")).hexdigest()
        for key, value in sorted(official_fields.items())
    }
    source_layers_agree = len(
        {canonical_hash(fields) for fields in parsed_layers.values()}
    ) == 1
    reference = next(iter(parsed_layers.values()))
    field_matches = {
        key: reference.get(key) == str(official_fields.get(key, ""))
        for key in ("instruction", "first_name", "last_name", "email", "phone_number")
    }
    return {
        "source_layer_count": len(source_queries),
        "source_layers_agree": source_layers_agree,
        "source_layer_hashes": layer_hashes,
        "official_field_hashes": official_hashes,
        "field_matches": field_matches,
        "identity_match": bool(source_layers_agree and all(field_matches.values())),
        "mismatched_fields": sorted(key for key, value in field_matches.items() if not value),
    }


def summarize_semantic_replays(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("Semantic replay summary requires rows")
    state_ids = [str(row["state_example_id"]) for row in rows]
    if len(state_ids) != len(set(state_ids)):
        raise ValueError("Semantic replay rows contain duplicate state keys")
    steps = [step for row in rows for step in row.get("steps", [])]
    history = [step for step in steps if not bool(step["is_target"])]
    targets = [step for step in steps if bool(step["is_target"])]
    jwt_reports = [
        report
        for step in steps
        for report in step.get("semantic_comparison", {}).get("jwt_reports", [])
    ]
    by_task: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_task[str(row["task_id"])].append(row)
    first_divergences = Counter(
        "none" if row.get("first_semantic_divergence_step") is None else str(row["first_semantic_divergence_step"])
        for row in rows
    )
    return {
        "format": "appworld_semantic_replay_summary_6h2_v1",
        "state_count": len(rows),
        "task_count": len(by_task),
        "identity_match_count": sum(bool(row["initial_task_identity_match"]) for row in rows),
        "complete_history_raw_match_count": sum(bool(row["complete_history_raw_match"]) for row in rows),
        "complete_history_v1_match_count": sum(bool(row["complete_history_v1_match"]) for row in rows),
        "complete_history_semantic_match_count": sum(bool(row["complete_history_semantic_match"]) for row in rows),
        "prior_observation_count": len(history),
        "prior_raw_match_count": sum(bool(step["raw_match"]) for step in history),
        "prior_v1_match_count": sum(bool(step["v1_match"]) for step in history),
        "prior_semantic_match_count": sum(bool(step["semantic_v2_match"]) for step in history),
        "target_observation_count": len(targets),
        "target_raw_match_count": sum(bool(step["raw_match"]) for step in targets),
        "target_v1_match_count": sum(bool(step["v1_match"]) for step in targets),
        "target_semantic_match_count": sum(bool(step["semantic_v2_match"]) for step in targets),
        "complete_semantic_replay_count": sum(bool(row["passed"]) for row in rows),
        "exception_count": sum(row.get("fatal_exception") is not None for row in rows),
        "jwt_field_count": len(jwt_reports),
        "temporal_only_jwt_count": sum(
            bool(report.get("semantic_match"))
            and bool(report.get("differing_claims"))
            and not bool(report.get("non_temporal_differing_claims"))
            for report in jwt_reports
        ),
        "non_temporal_jwt_mismatch_count": sum(
            bool(report.get("non_temporal_differing_claims"))
            or not bool(report.get("header_match", False))
            or not bool(report.get("stable_claims_match", False))
            for report in jwt_reports
        ),
        "non_token_mismatch_count": sum(
            int(step.get("semantic_comparison", {}).get("non_token_difference_count", 0))
            for step in steps
        ),
        "first_semantic_divergence_distribution": dict(sorted(first_divergences.items())),
        "per_task": {
            task_id: {
                "state_count": len(task_rows),
                "complete_semantic_replay_count": sum(bool(row["passed"]) for row in task_rows),
            }
            for task_id, task_rows in sorted(by_task.items())
        },
    }
