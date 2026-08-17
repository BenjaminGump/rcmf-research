from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.appworld_legacy_replay_6h1 import upgrade_replay_contract
from rcmf.training.appworld_semantic_replay_6h2 import (
    ALLOWED_TEMPORAL_CLAIMS,
    ALLOWED_TOKEN_FIELDS,
    SEMANTIC_NORMALIZATION_VERSION,
    canonical_hash,
    compare_observations_semantic,
    identity_hashes,
)
from rcmf.training.datasets import (
    _parse_appworld_state_text,
    load_decision_examples,
    load_memory_records,
)
from rcmf.training.state_conditioned_transition_6b import (
    AttemptLedger,
    initialize_or_validate_run_manifest,
)
from rcmf.training.transition_memory_6a import state_example_id
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _json_value(text: str) -> Any:
    normalized = str(text).replace("\r\n", "\n").strip()
    if normalized.startswith("Output:\n```") and normalized.endswith("```"):
        normalized = normalized[len("Output:\n```") : -3].strip()
    for loader in (json.loads, ast.literal_eval):
        try:
            return loader(normalized)
        except Exception:  # noqa: BLE001 - diagnostic extraction only
            continue
    return normalized


def _token_pairs(expected: Any, actual: Any, path: str = "$") -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        for key in sorted(set(expected) & set(actual), key=str):
            child = f"{path}.{key}"
            if str(key) == "access_token" and isinstance(expected[key], str) and isinstance(actual[key], str):
                output.append(
                    {"path": child, "expected": expected[key], "actual": actual[key]}
                )
            else:
                output.extend(_token_pairs(expected[key], actual[key], child))
    elif isinstance(expected, list) and isinstance(actual, list):
        for index, (left, right) in enumerate(zip(expected, actual)):
            output.extend(_token_pairs(left, right, f"{path}[{index}]"))
    return output


def _source_audit(api_lib: Path, fastapi_login: Path) -> dict[str, Any]:
    appworld_text = api_lib.read_text(encoding="utf-8")
    login_text = fastapi_login.read_text(encoding="utf-8")
    required_appworld = {
        "manager_uses_fixed_secret": 'LogInOutManager(\n            "SECRET"' in appworld_text,
        "subject_schema": 'data = {"sub": logging_manager.app_name + "+" + str(username)}' in appworld_text,
        "random_expiry_range": "random.randrange(10 * 60, 30 * 60)" in appworld_text,
        "create_access_token_call": "logging_manager.create_access_token(data=data, expires=expires_in)" in appworld_text,
        "validator_uses_get_payload": "payload = self._get_payload(token)" in appworld_text,
    }
    required_login = {
        "default_hs256": 'algorithm="HS256"' in login_text,
        "utc_clock": "datetime.now(timezone.utc) + expires" in login_text,
        "exp_claim_only": 'to_encode.update({"exp": expires_in})' in login_text,
        "jwt_encode": "jwt.encode(to_encode, self.secret.secret_for_encode, self.algorithm)" in login_text,
        "jwt_decode": "jwt.decode(" in login_text and "algorithms=[self.algorithm]" in login_text,
    }
    if not all(required_appworld.values()) or not all(required_login.values()):
        raise ValueError(
            f"Installed JWT source does not match the audited contract: {required_appworld=} {required_login=}"
        )
    dynamic_claim_mentions = {
        claim: bool(re.search(rf'["\']{claim}["\']', login_text))
        for claim in ("iat", "nbf", "jti")
    }
    if any(dynamic_claim_mentions.values()):
        raise ValueError(f"Unexpected dynamic JWT claim in installed source: {dynamic_claim_mentions}")
    return {
        "format": "appworld_auth_source_audit_6h2_v1",
        "generator": {
            "appworld_source_path_sha256": hashlib.sha256(str(api_lib).encode()).hexdigest(),
            "appworld_source_sha256": sha256_file(api_lib),
            "function": "appworld.apps.api_lib.login",
            "subject_schema": "<app_name>+<username>",
            "expiry_seconds": {"minimum_inclusive": 600, "maximum_exclusive": 1800},
            "random_function": "random.randrange",
            "manager_secret_source_sha256": hashlib.sha256(b"SECRET").hexdigest(),
            "manager_secret_provenance": "fixed_literal_in_setup_app_redacted",
        },
        "library": {
            "source_path_sha256": hashlib.sha256(str(fastapi_login).encode()).hexdigest(),
            "source_sha256": sha256_file(fastapi_login),
            "create_function": "fastapi_login.LoginManager.create_access_token",
            "validate_function": "fastapi_login.LoginManager._get_payload",
            "algorithm": "HS256",
            "clock": "datetime.now(timezone.utc)",
            "payload_claims_added_by_library": ["exp"],
            "validator": "jwt.decode_with_configured_secret_and_algorithm",
        },
        "allowed_temporal_claims": sorted(ALLOWED_TEMPORAL_CLAIMS),
        "dynamic_claim_mentions": dynamic_claim_mentions,
        "source_checks": {**required_appworld, **required_login},
    }


def _manifest_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for key in ("rows", "query_rows"):
        value = payload.get(key)
        if isinstance(value, list):
            return list(value)
    raise ValueError("Query manifest has no rows/query_rows")


def _legacy_history_observation_count(summary: Mapping[str, Any]) -> int:
    """Read the immutable EXP-024R name while rejecting ambiguous summaries."""
    if "history_observation_count" not in summary:
        raise KeyError("EXP-024R summary has no history_observation_count")
    return int(summary["history_observation_count"])


def _row_map(rows: Sequence[Mapping[str, Any]], key: str = "state_example_id") -> dict[str, Mapping[str, Any]]:
    output: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        identity = str(row[key])
        if identity in output:
            raise ValueError(f"Duplicate manifest identity: {identity}")
        output[identity] = row
    return output


def _field_hashes_from_spec(path: Path) -> dict[str, str]:
    values = _load_json(path)
    supervisor = values["supervisor"]
    fields = {
        "instruction": str(values["instruction"]),
        "first_name": str(supervisor["first_name"]),
        "last_name": str(supervisor["last_name"]),
        "email": str(supervisor["email"]),
        "phone_number": str(supervisor["phone_number"]),
    }
    return {
        key: hashlib.sha256(value.encode()).hexdigest() for key, value in fields.items()
    }


def _build_identity_audit(
    *,
    settings: Mapping[str, Any],
    queries: Sequence[Mapping[str, Any]],
    examples: Sequence[Any],
    records: Sequence[Any],
    contracts: Mapping[str, Any],
    official_probe: Mapping[str, Any],
    exp020: Mapping[str, Any],
    exp024a_strata: Mapping[str, Any],
) -> dict[str, Any]:
    examples_by_id = {state_example_id(index, example): example for index, example in enumerate(examples)}
    records_by_task = {str(record.task_id): record for record in records}
    contracts_by_id = _row_map(contracts["rows"])
    official_by_task = {str(row["task_id"]): row for row in official_probe["rows"]}
    exp020_by_id = _row_map(_manifest_rows(exp020))
    exp024a_by_id = _row_map(_manifest_rows(exp024a_strata))
    rows = []
    mismatches = []
    for query in queries:
        state_id = str(query["state_example_id"])
        task_id = str(query["task_id"])
        example = examples_by_id[state_id]
        record = records_by_task[task_id]
        _, decision_query, _ = _parse_appworld_state_text(str(example.state_text))
        raw_query = str(record.raw_trajectory["query"])
        contract_row = contracts_by_id[state_id]
        contract = upgrade_replay_contract(_load_json(Path(str(contract_row["contract_path"]))))
        contract_query = str(contract["expected_task_query"])
        source_queries = {
            "decision_state_text": decision_query,
            "raw_successful_trajectory": raw_query,
            "exp024r_replay_contract": contract_query,
        }
        source_hashes = {name: identity_hashes(value) for name, value in source_queries.items()}
        source_full_hashes = {
            name: hashlib.sha256(value.encode()).hexdigest() for name, value in source_queries.items()
        }
        source_layers_agree = len(set(source_full_hashes.values())) == 1
        reference = source_hashes["decision_state_text"]
        official = official_by_task[task_id]
        official_hashes = dict(official["field_sha256"])
        field_matches = {
            key: reference[key] == str(official_hashes[key]) for key in sorted(reference)
        }
        task_id_checks = {
            "query_manifest": task_id == str(query["task_id"]),
            "decision_metadata": task_id == str(example.metadata.get("task_id")),
            "raw_trajectory_record": task_id == str(record.task_id),
            "contract": task_id == str(contract["task_id"]),
            "exp020_manifest": state_id in exp020_by_id and task_id == str(exp020_by_id[state_id]["task_id"]),
            "exp024a_manifest": state_id in exp024a_by_id and task_id == str(exp024a_by_id[state_id]["task_id"]),
            "official_task": task_id == str(official["task_id"]),
        }
        identity_match = bool(
            source_layers_agree and all(field_matches.values()) and all(task_id_checks.values())
        )
        row = {
            "state_example_id": state_id,
            "task_id": task_id,
            "step_id": int(query["step_id"]),
            "source_layer_full_query_sha256": source_full_hashes,
            "source_layer_field_sha256": source_hashes,
            "source_layers_agree": source_layers_agree,
            "official_field_sha256": official_hashes,
            "field_matches": field_matches,
            "mismatched_fields": sorted(key for key, value in field_matches.items() if not value),
            "task_id_checks": task_id_checks,
            "query_manifest_rows": {
                "exp020_sha256": canonical_hash(exp020_by_id[state_id]),
                "exp022_sha256": canonical_hash(query),
                "exp024a_sha256": canonical_hash(exp024a_by_id[state_id]),
            },
            "source_path_sha256": hashlib.sha256(
                str(record.metadata.get("source_path", "")).encode()
            ).hexdigest(),
            "official_task_files": official["task_files"],
            "official_initial_database_fingerprint": official["initial_database_fingerprint"],
            "identity_match": identity_match,
        }
        rows.append(row)
        if not identity_match:
            mismatches.append(row)

    mismatch_tasks = sorted({str(row["task_id"]) for row in mismatches})
    snapshot_rows = []
    matching_snapshot_count = 0
    for task_id in mismatch_tasks:
        source_reference = next(row for row in mismatches if row["task_id"] == task_id)
        expected_hashes = source_reference["source_layer_field_sha256"]["decision_state_text"]
        for root_text in settings["identity_snapshot_candidates"]:
            root = Path(str(root_text))
            path = root / "tasks" / task_id / "specs.json"
            if not path.exists():
                snapshot_rows.append(
                    {
                        "task_id": task_id,
                        "candidate_path_sha256": hashlib.sha256(str(path).encode()).hexdigest(),
                        "exists": False,
                    }
                )
                continue
            field_hashes = _field_hashes_from_spec(path)
            match = field_hashes == expected_hashes
            matching_snapshot_count += int(match)
            snapshot_rows.append(
                {
                    "task_id": task_id,
                    "candidate_path_sha256": hashlib.sha256(str(path).encode()).hexdigest(),
                    "exists": True,
                    "spec_sha256": sha256_file(path),
                    "field_sha256": field_hashes,
                    "matches_source_query_identity": match,
                }
            )

    mismatch_cause = None
    source_log = Path("experiments/outputs/legacy_react_code_agent/openai/gpt-4o-2024-05-13/train/tasks/b0a8eae_2/logs/environment_io.md")
    source_log_trace: dict[str, Any] = {
        "path_sha256": hashlib.sha256(str(source_log).encode()).hexdigest(),
        "exists": source_log.exists(),
    }
    if source_log.exists():
        source_log_text = source_log.read_text(encoding="utf-8")
        b0_row = next((row for row in mismatches if row["task_id"] == "b0a8eae_2"), None)
        if b0_row is not None:
            example = examples_by_id[str(b0_row["state_example_id"])]
            _, source_query, _ = _parse_appworld_state_text(str(example.state_text))
            source_log_trace.update(
                {
                    "sha256": sha256_file(source_log),
                    "contains_source_query": source_query in source_log_text,
                }
            )
    if mismatches:
        only_b0 = mismatch_tasks == ["b0a8eae_2"]
        b0_fields = set(mismatches[0]["mismatched_fields"]) if only_b0 else set()
        supervisor_only = b0_fields == {"first_name", "last_name", "email", "phone_number"}
        if only_b0 and supervisor_only and matching_snapshot_count == 0:
            mismatch_cause = "raw_successful_trajectory_query_supervisor_inconsistent_with_official_0_1_0_task_bundle"

    identity_gate_passed = not mismatches
    branch = (
        "identity_provenance_validated"
        if identity_gate_passed
        else "source_query_task_identity_snapshot_unresolved"
    )
    return {
        "format": "appworld_all_45_identity_provenance_audit_6h2_v1",
        "state_count": len(rows),
        "task_count": len({row["task_id"] for row in rows}),
        "identity_match_count": sum(bool(row["identity_match"]) for row in rows),
        "identity_mismatch_count": len(mismatches),
        "mismatch_state_ids": [row["state_example_id"] for row in mismatches],
        "mismatch_task_ids": mismatch_tasks,
        "mismatch_field_counts": dict(
            sorted(Counter(field for row in mismatches for field in row["mismatched_fields"]).items())
        ),
        "exact_cause": mismatch_cause,
        "matching_historical_snapshot_count": matching_snapshot_count,
        "snapshot_search": snapshot_rows,
        "source_log_trace": source_log_trace,
        "identity_gate_passed": identity_gate_passed,
        "decision_branch": branch,
        "rows": rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_semantic_replay_6h2.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp024r2")
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6h2"]
    persistent = Path(settings["persistent_root"])
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError(f"Persistent root is not mounted: {persistent}")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")

    parent_a = Path(settings["parent_exp024a"])
    parent_r = Path(settings["parent_exp024r"])
    exp020 = Path(settings["exp020_artifact"])
    exp022 = Path(settings["exp022_artifact"])
    source = Path(settings["source_data"])
    paths = {
        "parent_a_run_manifest": parent_a / "run_manifest.json",
        "parent_a_condition_manifest": parent_a / "condition_manifest.json",
        "parent_a_strata": parent_a / "audit_state_strata.json",
        "parent_r_run_manifest": parent_r / "run_manifest.json",
        "parent_r_environment": parent_r / "environment_provenance.json",
        "parent_r_sentinel_manifest": parent_r / "sentinel_manifest.json",
        "parent_r_sentinel_summary": parent_r / "replay" / "sentinel_summary_v2.json",
        "exp020_query_manifest": exp020 / "expanded_query_manifest.json",
        "exp022_query_manifest": exp022 / "one_step_query_manifest.json",
        "decision_examples": source / "decision_examples.jsonl",
        "memory_records": source / "memory_records.jsonl",
    }
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Immutable input missing: {name}={path}")
    parent_environment = _load_json(paths["parent_r_environment"])
    contracts_path = Path(str(parent_environment["active_contract_manifest"]))
    paths["parent_r_contract_manifest"] = contracts_path
    data_hashes = {name: sha256_file(path) for name, path in paths.items()}
    config_hash = sha256_file(args.config)
    initialize_or_validate_run_manifest(
        args.artifact_dir / "run_manifest.json",
        run_uuid=str(settings["run_uuid"]),
        config_sha256=config_hash,
        data_manifest_hashes=data_hashes,
        source_commit=args.lambda_head,
        command_scope=[
            "schema_limited_exp_only_jwt_semantic_equivalence",
            "all_45_source_identity_provenance_audit",
            "repeated_13_state_sentinel_only_after_identity_gate",
            "full_45_replay_only_after_sentinel_gate",
            "no_qwen_or_memory_conditions_or_training",
        ],
    )

    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="auth_source_and_identity_preflight",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_hash,
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        expected = settings["expected"]
        queries_payload = _load_json(paths["exp022_query_manifest"])
        queries = list(_manifest_rows(queries_payload))
        sentinel = _load_json(paths["parent_r_sentinel_manifest"])
        old_sentinel = _load_json(paths["parent_r_sentinel_summary"])
        condition_manifest = _load_json(paths["parent_a_condition_manifest"])
        checks = {
            "states": len(queries) == int(expected["state_count"]),
            "tasks": len({str(row["task_id"]) for row in queries}) == int(expected["task_count"]),
            "sentinel_states": int(sentinel["state_count"]) == int(expected["sentinel_state_count"]),
            "sentinel_prior_observations": _legacy_history_observation_count(old_sentinel)
            == int(expected["sentinel_prior_observation_count"]),
            "full_prior_observations": sum(int(row["step_id"]) - 1 for row in queries) == int(expected["full_prior_observation_count"]),
            "conditions": int(condition_manifest["condition_count"]) == int(expected["condition_count"]),
            "legacy_python": str(parent_environment["legacy_python"]) == str(settings["legacy"]["executable"]),
            "version": parent_environment["probe"]["appworld_version"] == "0.1.0",
            "db_version": parent_environment["probe"]["db_version"] == "0.1.0",
            "wheel": parent_environment["wheel"]["sha256"] == settings["legacy"]["wheel_sha256"],
        }
        if not all(checks.values()):
            raise ValueError(f"Immutable EXP-024A/R contract failed: {checks}")

        auth_source = _source_audit(
            Path(settings["legacy"]["appworld_api_lib"]),
            Path(settings["legacy"]["fastapi_login_source"]),
        )
        atomic_write_json(args.artifact_dir / "appworld_auth_source_audit.json", auth_source)

        old_state_dir = Path(settings["parent_exp024r"]) / "replay" / "states_v2"
        old_rows = [_load_json(path) for path in sorted(old_state_dir.glob("*.json"))]
        if len(old_rows) != int(expected["sentinel_state_count"]):
            raise ValueError("Immutable EXP-024R sentinel row count changed")
        semantic_reports = []
        sensitive_jwt_pairs = []
        for row in old_rows:
            for step in row["steps"]:
                report = compare_observations_semantic(
                    str(step["expected_raw_observation"]),
                    str(step["actual_raw_observation"]),
                )
                if report["jwt_field_count"]:
                    semantic_reports.append(
                        {
                            "state_example_id": row["state_example_id"],
                            "task_id": row["task_id"],
                            "step_id": step["step_id"],
                            "is_target": step["is_target"],
                            "report": report,
                        }
                    )
                later = [
                    candidate
                    for candidate in row["steps"]
                    if int(candidate["step_id"]) > int(step["step_id"])
                    and "access_token" in str(candidate.get("action", ""))
                ]
                for pair_index, pair in enumerate(
                    _token_pairs(
                        _json_value(str(step["expected_raw_observation"])),
                        _json_value(str(step["actual_raw_observation"])),
                    )
                ):
                    sensitive_jwt_pairs.append(
                        {
                            "pair_id": f"{row['state_example_id']}:{step['step_id']}:{pair_index}",
                            "state_example_id": row["state_example_id"],
                            "task_id": row["task_id"],
                            "step_id": step["step_id"],
                            "expected_token": pair["expected"],
                            "actual_token": pair["actual"],
                            "subsequent_authenticated_action_count": len(later),
                            "subsequent_authenticated_actions_exception_free": all(
                                candidate.get("exception") is None for candidate in later
                            ),
                        }
                    )

        private_dir = args.artifact_dir / "private"
        private_dir.mkdir(parents=True, exist_ok=True)
        probe_request = {
            "format": "appworld_identity_probe_request_6h2_v1",
            "legacy_python": settings["legacy"]["executable"],
            "appworld_root": settings["legacy"]["appworld_root"],
            "experiment_prefix": f"exp024r2_{_safe_name(args.attempt_id)}_identity",
            "random_seed": int(settings["replay"]["random_seed"]),
            "max_interactions": int(settings["replay"]["max_interactions"]),
            "max_api_calls_per_interaction": int(settings["replay"]["max_api_calls_per_interaction"]),
            "task_ids": sorted({str(row["task_id"]) for row in queries}),
            "jwt_pairs": sensitive_jwt_pairs,
        }
        probe_input = private_dir / f"identity_probe_input_{_safe_name(args.attempt_id)}.json"
        probe_output = args.artifact_dir / "identity_probe.json"
        atomic_write_json(probe_input, probe_request)
        if probe_output.exists():
            official_probe = _load_json(probe_output)
            if official_probe.get("request_sha256") != canonical_hash(probe_request):
                raise ValueError("Existing identity probe belongs to another request")
        else:
            env = dict(os.environ)
            env.update(
                {
                    "APPWORLD_ROOT": str(settings["legacy"]["appworld_root"]),
                    "APPWORLD_CACHE": str(settings["legacy"]["appworld_cache"]),
                    "PYTHONNOUSERSITE": "1",
                    "PYTHONPATH": "",
                    "PYTHONUNBUFFERED": "1",
                }
            )
            command = [
                str(settings["legacy"]["executable"]),
                str(settings["replay"]["identity_bridge"]),
                "--input",
                str(probe_input),
                "--output",
                str(probe_output),
            ]
            completed = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=int(settings["replay"]["subprocess_timeout_seconds"]) * int(expected["task_count"]),
                check=False,
            )
            (args.artifact_dir / "logs").mkdir(parents=True, exist_ok=True)
            (args.artifact_dir / "logs" / "identity_probe.log").write_text(
                completed.stdout, encoding="utf-8"
            )
            if completed.returncode != 0 or not probe_output.exists():
                raise RuntimeError("Legacy identity/JWT probe failed")
            official_probe = _load_json(probe_output)
        attempt.progress(latest_validated_checkpoint=str(probe_output))

        direct_by_id = {str(row["pair_id"]): row for row in official_probe["jwt_rows"]}
        jwt_audit_rows = []
        non_temporal = 0
        for source_report in semantic_reports:
            for index, jwt_report in enumerate(source_report["report"]["jwt_reports"]):
                pair_id = f"{source_report['state_example_id']}:{source_report['step_id']}:{index}"
                direct = direct_by_id[pair_id]
                non_temporal += int(bool(jwt_report["non_temporal_differing_claims"]))
                jwt_audit_rows.append(
                    {
                        "pair_id": pair_id,
                        "state_example_id": source_report["state_example_id"],
                        "task_id": source_report["task_id"],
                        "step_id": source_report["step_id"],
                        "is_target": source_report["is_target"],
                        "semantic_report": jwt_report,
                        "installed_validator": direct,
                    }
                )
        jwt_audit = {
            "format": "appworld_sentinel_jwt_stable_claim_audit_6h2_v1",
            "sentinel_state_count": len(old_rows),
            "jwt_pair_count": len(jwt_audit_rows),
            "allowed_temporal_claims": sorted(ALLOWED_TEMPORAL_CLAIMS),
            "allowed_token_fields": sorted(ALLOWED_TOKEN_FIELDS),
            "non_temporal_mismatch_count": non_temporal,
            "all_headers_match": all(row["semantic_report"]["header_match"] for row in jwt_audit_rows),
            "all_stable_claims_match": all(row["semantic_report"]["stable_claims_match"] for row in jwt_audit_rows),
            "all_expected_tokens_validate": all(
                row["installed_validator"]["expected"]["payload_validator_accepted"]
                and row["installed_validator"]["expected"]["current_user_validator_accepted"]
                for row in jwt_audit_rows
            ),
            "all_actual_tokens_validate": all(
                row["installed_validator"]["actual"]["payload_validator_accepted"]
                and row["installed_validator"]["actual"]["current_user_validator_accepted"]
                for row in jwt_audit_rows
            ),
            "actual_tokens_accepted_by_subsequent_recorded_calls": all(
                row["installed_validator"]["subsequent_authenticated_actions_exception_free"]
                for row in jwt_audit_rows
            ),
            "rows": jwt_audit_rows,
        }
        jwt_audit["hard_gate_passed"] = bool(
            jwt_audit["jwt_pair_count"] == 11
            and jwt_audit["non_temporal_mismatch_count"] == 0
            and jwt_audit["all_headers_match"]
            and jwt_audit["all_stable_claims_match"]
            and jwt_audit["all_expected_tokens_validate"]
            and jwt_audit["all_actual_tokens_validate"]
        )
        atomic_write_json(args.artifact_dir / "jwt_stable_claim_audit.json", jwt_audit)
        if not jwt_audit["hard_gate_passed"]:
            atomic_write_json(
                args.artifact_dir / "preflight_decision.json",
                {
                    "decision_branch": "jwt_mismatch_not_temporal_only",
                    "identity_gate_passed": False,
                    "sentinel_allowed": False,
                    "full_replay_allowed": False,
                },
            )
            attempt.progress(
                latest_validated_checkpoint=str(args.artifact_dir / "preflight_decision.json")
            )
            return

        examples = load_decision_examples(paths["decision_examples"])
        records = load_memory_records(paths["memory_records"])
        identity_audit = _build_identity_audit(
            settings=settings,
            queries=queries,
            examples=examples,
            records=records,
            contracts=_load_json(contracts_path),
            official_probe=official_probe,
            exp020=_load_json(paths["exp020_query_manifest"]),
            exp024a_strata=_load_json(paths["parent_a_strata"]),
        )
        atomic_write_json(args.artifact_dir / "identity_provenance_audit.json", identity_audit)
        decision = {
            "format": "appworld_semantic_replay_preflight_decision_6h2_v1",
            "decision_branch": identity_audit["decision_branch"],
            "jwt_gate_passed": True,
            "identity_gate_passed": bool(identity_audit["identity_gate_passed"]),
            "sentinel_allowed": bool(identity_audit["identity_gate_passed"]),
            "full_replay_allowed": False,
            "normalization_version": SEMANTIC_NORMALIZATION_VERSION,
            "scientific_parameter_changed": False,
            "qwen_import_or_forward_count": 0,
        }
        atomic_write_json(args.artifact_dir / "preflight_decision.json", decision)
        attempt.progress(latest_validated_checkpoint=str(args.artifact_dir / "preflight_decision.json"))
        print(json.dumps(decision, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
