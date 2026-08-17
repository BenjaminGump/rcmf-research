from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _render_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Environment / contract | Identity | Histories | Prior observations | Targets | Complete |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {name} | {identity} | {histories} | {prior} | {targets} | {complete} |".format(
                **row
            )
        )
    return "\n".join(lines)


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
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    required = {
        "run_manifest": args.artifact_dir / "run_manifest.json",
        "auth_source": args.artifact_dir / "appworld_auth_source_audit.json",
        "jwt_audit": args.artifact_dir / "jwt_stable_claim_audit.json",
        "identity": args.artifact_dir / "identity_provenance_audit.json",
        "preflight": args.artifact_dir / "preflight_decision.json",
    }
    for name, path in required.items():
        if not path.exists():
            raise FileNotFoundError(f"Analysis prerequisite missing: {name}={path}")
    data_hashes = {name: sha256_file(path) for name, path in required.items()}
    config_hash = sha256_file(args.config)
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="semantic_replay_analysis",
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
        auth = _load_json(required["auth_source"])
        jwt = _load_json(required["jwt_audit"])
        identity = _load_json(required["identity"])
        preflight = _load_json(required["preflight"])
        parent_r = Path(settings["parent_exp024r"])
        old_01 = _load_json(parent_r / "replay" / "sentinel_summary_v2.json")
        old_paired = _load_json(parent_r / "paired_0_2_vs_0_1_comparison.json")
        sentinel_path = args.artifact_dir / "replay" / "semantic_sentinel_summary.json"
        full_path = args.artifact_dir / "replay" / "full_semantic_replay_summary.json"
        sentinel = _load_json(sentinel_path) if sentinel_path.exists() else None
        full = _load_json(full_path) if full_path.exists() else None

        if full is not None:
            branch = str(full["decision"]["decision_branch"])
        elif sentinel is not None and not bool(sentinel["decision"]["sentinel_gate_passed"]):
            branch = str(sentinel["decision"]["decision_branch"])
        else:
            branch = str(preflight["decision_branch"])
        semantic_validated = branch == "appworld_010_semantic_replay_validated"

        old_02 = old_paired["appworld_0_2_dev0"]
        paired_rows = [
            {
                "name": "AppWorld 0.2.0.dev0 / v1 (13 sentinel)",
                "identity": "not recorded by EXP-024A",
                "histories": f"{old_02['complete_history_match_count']}/13",
                "prior": f"{old_02['history_observation_match_count']}/102",
                "targets": f"{old_02['target_observation_match_count']}/13",
                "complete": f"{old_02['complete_replay_pass_count']}/13",
            },
            {
                "name": "AppWorld 0.1.0 / v1 (13 sentinel)",
                "identity": f"{old_01['initial_identity_match_count']}/13",
                "histories": f"{old_01['complete_history_match_count']}/13",
                "prior": f"{old_01['history_observation_match_count']}/102",
                "targets": f"{old_01['target_observation_match_count']}/13",
                "complete": f"{old_01['complete_replay_pass_count']}/13",
            },
        ]
        if sentinel is None:
            paired_rows.append(
                {
                    "name": "AppWorld 0.1.0 / semantic v2",
                    "identity": "not run",
                    "histories": "not run",
                    "prior": "not run",
                    "targets": "not run",
                    "complete": f"blocked: {branch}",
                }
            )
        else:
            summary = sentinel["repeat_summaries"][0]
            paired_rows.append(
                {
                    "name": "AppWorld 0.1.0 / semantic v2 (13 sentinel)",
                    "identity": f"{summary['identity_match_count']}/13",
                    "histories": f"{summary['complete_history_semantic_match_count']}/13",
                    "prior": f"{summary['prior_semantic_match_count']}/102",
                    "targets": f"{summary['target_semantic_match_count']}/13",
                    "complete": f"{summary['complete_semantic_replay_count']}/13",
                }
            )

        auth_report = "\n".join(
            [
                "# AppWorld 0.1.0 Authentication Source Audit",
                "",
                f"- AppWorld source SHA256: `{auth['generator']['appworld_source_sha256']}`",
                f"- fastapi-login source SHA256: `{auth['library']['source_sha256']}`",
                f"- Generator: `{auth['generator']['function']}`",
                f"- Validator: `{auth['library']['validate_function']}`",
                f"- Algorithm: `{auth['library']['algorithm']}`",
                f"- Clock: `{auth['library']['clock']}`",
                f"- Payload schema: `sub=<app>+<username>` plus `exp`",
                f"- Expiration range: `[600, 1800)` seconds",
                f"- Secret provenance hash: `{auth['generator']['manager_secret_source_sha256']}`",
                "- No `iat`, `nbf`, or `jti` claim generation was found.",
            ]
        )
        semantic_spec = "\n".join(
            [
                "# JWT Semantic Normalization Specification",
                "",
                "Version: `appworld_observation_semantic_normalization_6h2_v1`.",
                "",
                "The comparator first applies locked v1 normalization. It then interprets a string as a JWT only when both sides are valid three-segment JWTs under an explicitly allowed `access_token` field. Header, `sub`, every other stable claim, and temporal-claim presence remain exact. Only the numeric value of `exp` and the consequent signature bytes are omitted from semantic identity.",
                "",
                f"- Audited JWT pairs: `{jwt['jwt_pair_count']}`",
                f"- Non-temporal mismatches: `{jwt['non_temporal_mismatch_count']}`",
                f"- Expected tokens accepted by installed validator: `{jwt['all_expected_tokens_validate']}`",
                f"- Actual tokens accepted by installed validator: `{jwt['all_actual_tokens_validate']}`",
                f"- Hard gate: `{jwt['hard_gate_passed']}`",
            ]
        )
        identity_report = "\n".join(
            [
                "# All-45 Identity Provenance Audit",
                "",
                f"- Identity matches: `{identity['identity_match_count']}/{identity['state_count']}`",
                f"- Mismatch states: `{identity['mismatch_state_ids']}`",
                f"- Mismatch fields: `{identity['mismatch_field_counts']}`",
                f"- Matching historical task snapshots: `{identity['matching_historical_snapshot_count']}`",
                f"- Exact cause: `{identity['exact_cause']}`",
                f"- Decision: `{identity['decision_branch']}`",
                "",
                "The decision state, raw successful trajectory, and replay contract agree with one another. The immutable historical 0.1.0 task backup and reconstructed 0.1.0 capsule agree with each other, but disagree with those source layers on all four supervisor identity fields for `b0a8eae_2`; the task instruction matches.",
            ]
        )
        sentinel_report = (
            "# Repeated 13-State Sentinel\n\n"
            + (
                f"Not run because the all-45 identity gate stopped at `{branch}`.\n"
                if sentinel is None
                else f"Decision: `{sentinel['decision']['decision_branch']}`; repeat semantic matches: `{sentinel['repeat_semantic_match_count']}/13`.\n"
            )
        )
        full_report = (
            "# Full 45-State Semantic Replay\n\n"
            + (
                f"Not run because prerequisite gates stopped at `{branch}`.\n"
                if full is None
                else f"Decision: `{full['decision']['decision_branch']}`; complete semantic replay: `{full['summary']['complete_semantic_replay_count']}/45`.\n"
            )
        )
        paired_report = "# v1 Versus Semantic-v2 Replay\n\n" + _render_table(paired_rows) + "\n\nSemantic v2 is prospective and does not retroactively change EXP-024R's exact v1 result.\n"
        future_contract = """# Future Replay-Prompt Contract

Future Qwen generation remains blocked in EXP-024R2. After a separately reviewed gate pass, generation must use the actual current AppWorld 0.1.0 replay observations, the generated action must execute in that same replayed world, and historical JWT strings must never be inserted into a world containing newly issued tokens. Semantic-v2 equality establishes state equivalence only for the explicitly audited `exp` timing field.
"""
        summary = {
            "format": "appworld_semantic_replay_final_summary_6h2_v1",
            "run_uuid": settings["run_uuid"],
            "source_commit": args.lambda_head,
            "parent_exp024r": settings["parent_exp024r"],
            "decision_branch": branch,
            "semantic_replay_validated": semantic_validated,
            "generation_remains_blocked": True,
            "auth_source_audit": auth,
            "jwt_stable_claim_audit": {
                key: jwt[key]
                for key in (
                    "jwt_pair_count",
                    "allowed_temporal_claims",
                    "non_temporal_mismatch_count",
                    "all_headers_match",
                    "all_stable_claims_match",
                    "all_expected_tokens_validate",
                    "all_actual_tokens_validate",
                    "hard_gate_passed",
                )
            },
            "identity_audit": {
                key: identity[key]
                for key in (
                    "state_count",
                    "task_count",
                    "identity_match_count",
                    "identity_mismatch_count",
                    "mismatch_state_ids",
                    "mismatch_field_counts",
                    "matching_historical_snapshot_count",
                    "exact_cause",
                    "decision_branch",
                )
            },
            "sentinel": sentinel if sentinel is not None else "not_run_blocked_by_identity_gate",
            "full_replay": full if full is not None else "not_run_blocked_by_prerequisite_gate",
            "qwen_import_forward_generation_count": 0,
            "scientific_parameter_changed": False,
        }
        final_report = "\n".join(
            [
                "# EXP-024R2 Final Report",
                "",
                f"Decision branch: `{branch}`.",
                "",
                f"JWT semantic contract gate: `{'passed' if jwt['hard_gate_passed'] else 'failed'}` with `{jwt['non_temporal_mismatch_count']}` non-temporal mismatches.",
                f"All-45 identity gate: `{identity['identity_match_count']}/{identity['state_count']}`.",
                f"Repeated sentinel: `{'completed' if sentinel else 'not run'}`.",
                f"Full 45 replay: `{'completed' if full else 'not run'}`.",
                "",
                "No Qwen model was loaded or run, no memory condition was executed, and no training or AppWorld task evaluation occurred.",
            ]
        )
        outputs = {
            "appworld_auth_source_audit.md": auth_report,
            "jwt_semantic_normalization_spec.md": semantic_spec,
            "identity_provenance_audit_report.md": identity_report,
            "repeated_sentinel_report.md": sentinel_report,
            "full_semantic_replay_report.md": full_report,
            "v1_vs_v2_paired_report.md": paired_report,
            "future_replay_prompt_contract.md": future_contract,
            "final_exp024r2_report.md": final_report,
        }
        for name, text in outputs.items():
            atomic_write_text(args.artifact_dir / name, text.rstrip() + "\n")
        atomic_write_json(args.artifact_dir / "final_exp024r2_summary.json", summary)
        attempt.progress(
            latest_validated_checkpoint=str(args.artifact_dir / "final_exp024r2_summary.json")
        )
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
