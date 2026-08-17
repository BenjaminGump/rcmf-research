from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.appworld_legacy_replay_6h1 import canonical_hash
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_legacy_replay_6h1.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6h1"]
    if os.name != "nt" and not os.path.ismount(Path(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    required = (
        "run_manifest.json",
        "attempts.jsonl",
        "heartbeat.json",
        "environment_provenance.json",
        "sentinel_manifest.json",
        "replay/sentinel_summary.json",
        "final_exp024r_summary.json",
        "final_exp024r_report.md",
        "paired_0_2_vs_0_1_comparison.json",
        "environment_provenance_report.md",
        "sentinel_replay_report.md",
        "legacy_45_state_replay_report.md",
    )
    checks: dict[str, bool] = {
        f"required:{name}": (args.artifact_dir / name).exists() for name in required
    }
    if not all(checks.values()):
        raise FileNotFoundError(
            f"Missing artifacts: {[key for key, value in checks.items() if not value]}"
        )
    run_manifest = _load_json(args.artifact_dir / "run_manifest.json")
    environment = _load_json(args.artifact_dir / "environment_provenance.json")
    active_contract_manifest = Path(str(environment["active_contract_manifest"]))
    if not active_contract_manifest.exists():
        raise FileNotFoundError(
            f"Active replay contract manifest missing: {active_contract_manifest}"
        )
    contracts = _load_json(active_contract_manifest)
    sentinel = _load_json(args.artifact_dir / "sentinel_manifest.json")
    final = _load_json(args.artifact_dir / "final_exp024r_summary.json")
    attempts = list(read_jsonl(args.artifact_dir / "attempts.jsonl"))
    replay_path = args.artifact_dir / "replay" / "replay_summary.json"
    replay = (
        _load_json(replay_path)
        if replay_path.exists()
        else _load_json(args.artifact_dir / "replay" / "sentinel_summary.json")
    )
    state_paths = sorted((args.artifact_dir / "replay" / "states").glob("*.json"))
    state_rows = [_load_json(path) for path in state_paths]
    state_ids = [str(row["state_example_id"]) for row in state_rows]
    events = Counter((str(row["attempt_id"]), str(row["event"])) for row in attempts)
    attempt_ids = {str(row["attempt_id"]) for row in attempts}
    starts = {key for key, event in events if event == "start"}
    ends = {key for key, event in events if event == "end"}
    expected = settings["expected"]
    full_branch = final["decision_branch"] == "appworld_010_replay_validated"
    checks.update(
        {
            "run_uuid": run_manifest["run_uuid"] == settings["run_uuid"],
            "source_head": run_manifest["initial_source_commit"] == args.expected_head,
            "config_hash": run_manifest["config_sha256"] == sha256_file(args.config),
            "contract_states": contracts["state_count"] == expected["state_count"],
            "contract_tasks": contracts["task_count"] == expected["task_count"],
            "contract_prior_observations": contracts["prior_observation_count"]
            == expected["prior_observation_count"],
            "contract_unique_states": len(
                {str(row["state_example_id"]) for row in contracts["rows"]}
            )
            == expected["state_count"],
            "sentinel_hash": sentinel["manifest_sha256"]
            == canonical_hash(
                {key: value for key, value in sentinel.items() if key != "manifest_sha256"}
            ),
            "sentinel_has_no_history": sentinel["no_history_state_count"] >= 2,
            "wheel_hash": environment["wheel"]["sha256"] == settings["legacy"]["wheel_sha256"],
            "package_version": environment["probe"]["appworld_version"]
            == expected["package_version"],
            "data_version": environment["probe"]["db_version"] == expected["data_version"],
            "evaluation_version": environment["source_versions"]["versions"]["evaluation"]
            == [expected["evaluation_version"]],
            "isolated_python": "appworld-0.1.0-replay" in environment["legacy_python"],
            "isolated_root": environment["legacy_root"] == settings["legacy"]["root"],
            "verify_tests": bool(environment["official_verification"]["tests"]["verified_pass"]),
            "verify_tasks": bool(environment["official_verification"]["tasks"]["verified_pass"]),
            "attempt_unique_events": all(count == 1 for count in events.values()),
            "attempt_start_end_pairs": starts == ends == attempt_ids,
            "state_key_unique": len(state_ids) == len(set(state_ids)),
            "state_count_matches_summary": len(state_rows) == replay["state_count"],
            "no_qwen_import": final["qwen_import_count"] == 0,
            "no_qwen_forward": final["qwen_forward_count"] == 0,
            "no_qwen_generation": final["qwen_generation_count"] == 0,
            "no_memory_conditions": final["memory_condition_execution_count"] == 0,
            "full_gate_consistency": (not full_branch)
            or (
                replay["state_count"] == expected["state_count"]
                and replay["complete_replay_pass_count"] == expected["state_count"]
                and replay["history_observation_match_count"] == expected["prior_observation_count"]
                and replay["target_observation_match_count"] == expected["state_count"]
            ),
        }
    )
    validation = {
        "format": "appworld_legacy_replay_postrun_validation_6h1_v1",
        "all_checks_pass": all(checks.values()),
        "checks": checks,
        "decision_branch": final["decision_branch"],
        "source_commit": args.expected_head,
    }
    if not validation["all_checks_pass"]:
        raise ValueError(
            f"EXP-024R validation failed: {[key for key, value in checks.items() if not value]}"
        )
    atomic_write_json(args.artifact_dir / "postrun_validation.json", validation)
    atomic_write_text(
        args.artifact_dir / "postrun_validation.md",
        "# EXP-024R Post-run Validation\n\nAll checks passed.\n",
    )
    print(json.dumps(validation, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
