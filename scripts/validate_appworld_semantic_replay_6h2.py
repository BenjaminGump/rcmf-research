from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _legacy_task_source_manifest_hashes(
    task_manifests: Mapping[str, Any],
) -> dict[str, str]:
    output: dict[str, str] = {}
    for task_id, value in task_manifests.items():
        candidates = value if isinstance(value, list) else [value]
        source_suffix = f"/data/tasks/{task_id}"
        source_rows = [
            row
            for row in candidates
            if isinstance(row, Mapping)
            and str(row.get("root", "")).replace("\\", "/").endswith(source_suffix)
        ]
        if len(source_rows) != 1:
            raise ValueError(
                f"Expected one immutable source-task manifest for {task_id}, found {len(source_rows)}"
            )
        output[str(task_id)] = str(source_rows[0]["manifest_sha256"])
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_semantic_replay_6h2.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_config(args.config).raw["stage_c_6h2"]
    required = [
        "run_manifest.json",
        "attempts.jsonl",
        "appworld_auth_source_audit.json",
        "jwt_stable_claim_audit.json",
        "identity_probe.json",
        "identity_provenance_audit.json",
        "preflight_decision.json",
        "appworld_auth_source_audit.md",
        "jwt_semantic_normalization_spec.md",
        "identity_provenance_audit_report.md",
        "repeated_sentinel_report.md",
        "full_semantic_replay_report.md",
        "v1_vs_v2_paired_report.md",
        "future_replay_prompt_contract.md",
        "final_exp024r2_summary.json",
        "final_exp024r2_report.md",
    ]
    missing = [name for name in required if not (args.artifact_dir / name).exists()]
    if missing:
        raise FileNotFoundError(f"EXP-024R2 required artifacts missing: {missing}")
    run_manifest = _load_json(args.artifact_dir / "run_manifest.json")
    auth = _load_json(args.artifact_dir / "appworld_auth_source_audit.json")
    jwt = _load_json(args.artifact_dir / "jwt_stable_claim_audit.json")
    probe = _load_json(args.artifact_dir / "identity_probe.json")
    identity = _load_json(args.artifact_dir / "identity_provenance_audit.json")
    preflight = _load_json(args.artifact_dir / "preflight_decision.json")
    final = _load_json(args.artifact_dir / "final_exp024r2_summary.json")
    attempts = read_jsonl(args.artifact_dir / "attempts.jsonl")
    events = Counter((str(row["attempt_id"]), str(row["event"])) for row in attempts)
    attempt_ids = sorted({str(row["attempt_id"]) for row in attempts})
    completed_attempts = all(
        events[(attempt_id, "start")] == 1 and events[(attempt_id, "end")] == 1
        for attempt_id in attempt_ids
    )
    sentinel_path = args.artifact_dir / "replay" / "semantic_sentinel_summary.json"
    full_path = args.artifact_dir / "replay" / "full_semantic_replay_summary.json"
    identity_blocked = not bool(identity["identity_gate_passed"])
    parent_environment = _load_json(
        Path(settings["parent_exp024r"]) / "environment_provenance.json"
    )
    parent_task_hashes = _legacy_task_source_manifest_hashes(
        parent_environment["task_manifests"]
    )
    probe_task_hashes = {
        str(row["task_id"]): str(row["task_files"]["sha256"])
        for row in probe["rows"]
    }
    checks = {
        "run_uuid": run_manifest["run_uuid"] == settings["run_uuid"],
        "attempt_ledger_complete": completed_attempts,
        "attempt_ledger_nonempty": bool(attempt_ids),
        "auth_algorithm_hs256": auth["library"]["algorithm"] == "HS256",
        "auth_clock_exact": auth["library"]["clock"] == "datetime.now(timezone.utc)",
        "temporal_claims_exp_only": jwt["allowed_temporal_claims"] == ["exp"],
        "jwt_pairs": int(jwt["jwt_pair_count"]) == 11,
        "jwt_non_temporal_zero": int(jwt["non_temporal_mismatch_count"]) == 0,
        "jwt_gate": bool(jwt["hard_gate_passed"]),
        "all_expected_tokens_validate": bool(jwt["all_expected_tokens_validate"]),
        "all_actual_tokens_validate": bool(jwt["all_actual_tokens_validate"]),
        "identity_states": int(identity["state_count"]) == int(settings["expected"]["state_count"]),
        "identity_tasks": int(identity["task_count"]) == int(settings["expected"]["task_count"]),
        "identity_decision_consistent": preflight["decision_branch"] == identity["decision_branch"],
        "legacy_task_files_unchanged": probe_task_hashes == parent_task_hashes,
        "legacy_executable": probe["python_executable"] == settings["legacy"]["executable"],
        "legacy_version": probe["appworld_version"] == "0.1.0" and probe["db_version"] == "0.1.0",
        "qwen_zero": int(final["qwen_import_forward_generation_count"]) == 0,
        "scientific_parameters_unchanged": not bool(final["scientific_parameter_changed"]),
        "final_branch": final["decision_branch"] == preflight["decision_branch"],
        "generation_blocked": bool(final["generation_remains_blocked"]),
        "identity_block_prevents_sentinel": (not identity_blocked) or not sentinel_path.exists(),
        "identity_block_prevents_full": (not identity_blocked) or not full_path.exists(),
    }
    if not all(checks.values()):
        raise ValueError(f"EXP-024R2 postrun validation failed: {checks}")
    payload = {
        "format": "appworld_semantic_replay_postrun_validation_6h2_v1",
        "passed": True,
        "checks": checks,
        "attempt_ids": attempt_ids,
        "decision_branch": final["decision_branch"],
        "artifact_hashes": {
            name: sha256_file(args.artifact_dir / name)
            for name in required
            if name != "attempts.jsonl"
        },
    }
    atomic_write_json(args.artifact_dir / "postrun_validation.json", payload)
    atomic_write_text(
        args.artifact_dir / "postrun_validation.md",
        "# EXP-024R2 Postrun Validation\n\n"
        f"All `{len(checks)}` checks passed. Decision: `{final['decision_branch']}`.\n",
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
