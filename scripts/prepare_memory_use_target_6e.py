from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

from rcmf.config import load_config, save_resolved_config
from rcmf.factory import build_backend
from rcmf.training.datasets import (
    _appworld_messages_from_example,
    _render_prompt_with_metadata,
    load_decision_examples,
)
from rcmf.training.memory_use_target_6e import (
    ACTION_SIGNATURE_VERSION,
    TARGET_AUDIT_VERSION,
    action_signature,
    add_relative_targets,
    decompose_locked_utility,
    messages_with_serialized_transition,
    pairwise_coverage,
    select_serialization_audit_pairs,
)
from rcmf.training.state_conditioned_transition_6b import (
    AttemptLedger,
    initialize_or_validate_run_manifest,
)
from rcmf.training.transition_memory_6a import state_example_id
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_jsonl,
)
from scripts.prepare_all_task_data_6d import _ensure_backend_tokenizer
from scripts.run_raw_text_teacher_pilot import _target_token_ids, _token_ids


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows at {path}")
    return rows


def _assert_count(name: str, actual: int, expected: int) -> None:
    if int(actual) != int(expected):
        raise ValueError(f"Immutable EXP-020 {name} differs: {actual} != {expected}")


def _query_example_map(
    examples: list[Any], query_manifest: Mapping[str, Any]
) -> dict[str, tuple[int, Any]]:
    output = {}
    for row in query_manifest["query_rows"]:
        index = int(row["example_index"])
        example = examples[index]
        identity = state_example_id(index, example)
        if identity != str(row["state_example_id"]):
            raise ValueError(f"Query manifest identity mismatch: {identity}")
        output[identity] = (index, example)
    return output


def _serialize_preflight(
    *,
    tokenizer: Any,
    prompt_profile: str,
    selected: Sequence[Mapping[str, Any]],
    examples_by_id: Mapping[str, tuple[int, Any]],
    transitions_by_id: Mapping[str, Mapping[str, Any]],
    context_limit: int,
) -> list[dict[str, Any]]:
    context_cache: dict[str, dict[str, Any]] = {}
    output = []
    for row in selected:
        state_id = str(row["state_example_id"])
        if state_id not in context_cache:
            _, example = examples_by_id[state_id]
            messages = _appworld_messages_from_example(example, prompt_profile)
            prompt, _ = _render_prompt_with_metadata(tokenizer, messages, prompt_profile)
            target_ids = _target_token_ids(tokenizer, example)
            context_cache[state_id] = {
                "messages": messages,
                "base_prompt_sha256": sha256_text(prompt),
                "target_ids": target_ids,
                "target_token_sha256": sha256_text(
                    ",".join(str(value) for value in target_ids)
                ),
            }
        context = context_cache[state_id]
        transition = transitions_by_id[str(row["transition_id"])]
        for template in ("canonical_json", "compact_tagged"):
            messages = messages_with_serialized_transition(
                context["messages"], transition, prompt_profile, template
            )
            prompt, _ = _render_prompt_with_metadata(tokenizer, messages, prompt_profile)
            prompt_tokens = len(_token_ids(tokenizer, prompt))
            total = prompt_tokens + len(context["target_ids"])
            output.append(
                {
                    "format": "raw_transition_serialization_preflight_6e_v1",
                    "pair_id": str(row["pair_id"]),
                    "state_example_id": state_id,
                    "state_task_id": str(row["state_task_id"]),
                    "transition_id": str(row["transition_id"]),
                    "transition_parent_id": str(row["transition_parent_id"]),
                    "cell": str(row["cell"]),
                    "audit_selection_category": str(row["audit_selection_category"]),
                    "template": template,
                    "combined_prompt_tokens": prompt_tokens,
                    "target_tokens": len(context["target_ids"]),
                    "total_tokens_with_target": total,
                    "context_limit": int(context_limit),
                    "over_context": total > int(context_limit),
                    "truncated": False,
                    "base_prompt_sha256": context["base_prompt_sha256"],
                    "teacher_prompt_sha256": sha256_text(prompt),
                    "target_token_sha256": context["target_token_sha256"],
                    "transition_content_sha256": str(row["transition_content_sha256"]),
                }
            )
    return output


def _matched_pair_coverage(rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    by_state: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_state.setdefault(str(row["state_example_id"]), []).append(row)
    pair_count = 0
    states = 0
    for selected in by_state.values():
        local = 0
        for left in range(len(selected)):
            for right in range(left + 1, len(selected)):
                if abs(float(selected[left]["text_utility"]) - float(selected[right]["text_utility"])) < threshold:
                    continue
                a = selected[left]["transition_signature"]
                b = selected[right]["transition_signature"]
                if set(a["apps"]) & set(b["apps"]) and a["coarse_action_type"] == b["coarse_action_type"]:
                    local += 1
        pair_count += local
        states += local > 0
    return {
        "pair_count": pair_count,
        "state_count": states,
        "state_coverage": states / len(by_state) if by_state else 0.0,
    }


def _preflight_report(summary: Mapping[str, Any]) -> str:
    runtime = summary["serialization_runtime_projection"]
    counts = summary["serialization_counts"]
    return "\n".join(
        [
            "# EXP-021 Preflight and Locked-Target Decomposition",
            "",
            "## VERIFIED",
            "",
            f"- immutable EXP-020 scoreable rows: `{summary['contract']['scoreable_rows']}`",
            f"- A/B/C/D: `{summary['contract']['cells']}`",
            f"- serialization audit pairs: `{counts['audit_pairs']}` "
            f"(A `{counts['cell_a_pairs']}`, D `{counts['cell_d_pairs']}`)",
            f"- exact new frozen-Qwen forwards: `{counts['new_qwen_forwards']}`",
            f"- alternative-template over-context rows: `{counts['over_context_template_rows']}`",
            f"- no truncation: `true`",
            "",
            "## Projection",
            "",
            f"- best case: `{runtime['best_case_h100_hours']:.4f}` H100-hours",
            f"- expected: `{runtime['expected_h100_hours']:.4f}` H100-hours",
            f"- conservative: `{runtime['conservative_h100_hours']:.4f}` H100-hours",
            f"- expected artifact size: `{runtime['expected_artifact_mib']:.2f}` MiB",
            f"- review threshold exceeded: `{runtime['review_required']}`",
            "",
            "## Resume Plan",
            "",
            "- one immutable run UUID and append-only attempt ledger;",
            "- one fsync journal row per unique `(pair_id, template)` key;",
            "- template-0 scores are immutable references and are never rewritten;",
            "- L0 is reused from the validated EXP-020 cache;",
            "- final cache is atomically assembled only after duplicate/hash validation;",
            "- reconnects inspect heartbeat, tmux, process, and journal before resume.",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare EXP-021 target audit")
    parser.add_argument(
        "--config", type=Path,
        default=Path("configs/benchmark/stage_c_memory_use_target_6e.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="none-preflight")
    parser.add_argument("--parent-attempt-id", default=None)
    parser.add_argument("--resume-checkpoint", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6e"]
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    exp017 = Path(settings["exp017_artifact"])
    exp018 = Path(settings["exp018_artifact"])
    exp019 = Path(settings["exp019_artifact"])
    exp020 = Path(settings["exp020_artifact"])
    source = Path(settings["source_data"])
    paths = {
        "query_manifest": exp020 / "expanded_query_manifest.json",
        "pair_rows": exp020 / "two_axis_pair_rows.jsonl",
        "teacher_cache": exp020 / "teacher_cache.jsonl",
        "transition_panel": exp017 / "transition_panel.jsonl",
        "parent_split": exp018 / "transition_parent_split_manifest.json",
        "exp019_summary": exp019 / "final_summary.json",
        "exp020_summary": exp020 / "final_summary.json",
        "decision_examples": source / "decision_examples.jsonl",
    }
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Immutable input missing: {name}={path}")
    data_hashes = {name: sha256_file(path) for name, path in paths.items()}
    config_hash = sha256_file(args.config)
    initialize_or_validate_run_manifest(
        args.artifact_dir / "run_manifest.json",
        run_uuid=str(settings["run_uuid"]),
        config_sha256=config_hash,
        data_manifest_hashes=data_hashes,
        source_commit=args.lambda_head,
        command_scope=[
            "locked_raw_utility_decomposition",
            "action_signature_extraction",
            "serialization_robustness_192_pairs",
            "relative_intent_target_audit",
            "cached_field_and_cross_architectures",
        ],
    )
    save_resolved_config(cfg, args.artifact_dir / "resolved_config.yaml")
    atomic_write_json(args.artifact_dir / "stage_c_6e_settings.json", settings)
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]), attempt_id=args.attempt_id,
        phase="preflight_decomposition_and_serialization_manifest",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head, github_head=args.github_head,
        lambda_head=args.lambda_head, tmux_session=args.tmux_session,
        config_sha256=config_hash, data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        expected = settings["expected"]
        query_manifest = _load_json(paths["query_manifest"])
        pair_rows = _load_rows(paths["pair_rows"])
        panel_rows = _load_rows(paths["transition_panel"])
        _assert_count("query states", query_manifest["query_count"], expected["query_states"])
        _assert_count("train query states", query_manifest["train_query_count"], expected["train_query_states"])
        _assert_count("validation query states", query_manifest["validation_query_count"], expected["validation_query_states"])
        _assert_count("transitions", len(panel_rows), expected["transitions"])
        _assert_count("scoreable rows", len(pair_rows), expected["scoreable_rows"])
        cell_counts = Counter(str(row["cell"]) for row in pair_rows)
        for cell in "ABCD":
            _assert_count(f"cell {cell}", cell_counts[cell], expected[f"cell_{cell.lower()}"])
        if len({str(row["pair_id"]) for row in pair_rows}) != len(pair_rows):
            raise ValueError("Duplicate scoreable pair keys in immutable EXP-020 rows")
        if any(not bool(row["valid_for_loss"]) or bool(row["over_context"]) or bool(row["truncated"]) for row in pair_rows):
            raise ValueError("EXP-020 scoreable rows violate mask/no-truncation contract")

        examples = load_decision_examples(paths["decision_examples"])
        examples_by_id = _query_example_map(examples, query_manifest)
        transitions_by_id = {str(row["transition_id"]): row for row in panel_rows}
        query_signatures = {
            state_id: action_signature(example.target_text)
            for state_id, (_, example) in examples_by_id.items()
        }
        transition_signatures = {
            transition_id: action_signature(str(row["complete_action"]))
            for transition_id, row in transitions_by_id.items()
        }
        signature_rows = []
        for state_id, signature in sorted(query_signatures.items()):
            signature_rows.append({"kind": "query", "id": state_id, **signature})
        for transition_id, signature in sorted(transition_signatures.items()):
            signature_rows.append({"kind": "transition", "id": transition_id, **signature})
        write_jsonl(args.artifact_dir / "action_signatures.jsonl", signature_rows)

        enriched = []
        for row in pair_rows:
            copy = dict(row)
            copy["query_action_signature"] = query_signatures[str(row["state_example_id"])]
            copy["transition_signature"] = transition_signatures[str(row["transition_id"])]
            enriched.append(copy)
        enriched = add_relative_targets(
            enriched,
            scale_epsilon=float(settings["targets"]["robust_scale_epsilon"]),
            robust_clip=float(settings["targets"]["robust_clip"]),
        )
        write_jsonl(args.artifact_dir / "candidate_target_rows.jsonl", enriched)
        decomposition = decompose_locked_utility(enriched)
        atomic_write_json(args.artifact_dir / "locked_raw_utility_decomposition.json", decomposition)
        target_summary = {
            "format": TARGET_AUDIT_VERSION,
            "pairwise_coverage": pairwise_coverage(
                enriched, thresholds=settings["targets"]["pair_gap_coverage_thresholds"]
            ),
            "intent_matched_pair_coverage": _matched_pair_coverage(
                enriched, float(settings["targets"]["pair_gap_threshold"])
            ),
            "action_signature": {
                "format": ACTION_SIGNATURE_VERSION,
                "query_count": len(query_signatures),
                "transition_count": len(transition_signatures),
                "query_coarse_action_types": dict(Counter(value["coarse_action_type"] for value in query_signatures.values())),
                "transition_coarse_action_types": dict(Counter(value["coarse_action_type"] for value in transition_signatures.values())),
            },
        }
        atomic_write_json(args.artifact_dir / "candidate_target_summary.json", target_summary)

        selection = settings["serialization"]
        audit = select_serialization_audit_pairs(
            enriched, seed=int(settings["seed"]), cells=selection["cells"],
            pairs_per_cell=int(selection["pairs_per_cell"]),
            category_targets=selection["category_targets"],
        )
        atomic_write_json(args.artifact_dir / "serialization_audit_manifest.json", audit)
        tokenizer_backend = build_backend(cfg, load_model=False)
        tokenizer = _ensure_backend_tokenizer(tokenizer_backend)
        preflight_rows = _serialize_preflight(
            tokenizer=tokenizer, prompt_profile=cfg.benchmark.prompt_profile,
            selected=audit["rows"], examples_by_id=examples_by_id,
            transitions_by_id=transitions_by_id,
            context_limit=int(settings["context_limit"]),
        )
        write_jsonl(args.artifact_dir / "serialization_preflight.jsonl", preflight_rows)
        scoreable = sum(not bool(row["over_context"]) for row in preflight_rows)
        observed = float(settings["runtime"]["observed_teacher_seconds_per_forward"])
        best = scoreable * observed * float(settings["runtime"]["runtime_safety_factor_best"])
        expected_seconds = scoreable * observed * float(settings["runtime"]["runtime_safety_factor_expected"])
        conservative = scoreable * observed * float(settings["runtime"]["runtime_safety_factor_conservative"])
        expected_bytes = scoreable * int(settings["runtime"]["serialization_bytes_per_row"])
        free = shutil.disk_usage(args.artifact_dir).free
        runtime = {
            "best_case_h100_hours": best / 3600.0,
            "expected_h100_hours": expected_seconds / 3600.0,
            "conservative_h100_hours": conservative / 3600.0,
            "expected_artifact_bytes": expected_bytes,
            "expected_artifact_mib": expected_bytes / 2**20,
            "persistent_free_gib": free / 2**30,
            "review_threshold_h100_hours": float(settings["runtime"]["review_threshold_h100_hours"]),
            "review_required": expected_seconds / 3600.0 > float(settings["runtime"]["review_threshold_h100_hours"]),
        }
        summary = {
            "format": "memory_use_target_preflight_6e_v1",
            "status": "requires_runtime_approval" if runtime["review_required"] else "ready_for_serialization_scoring",
            "run_uuid": str(settings["run_uuid"]),
            "source_commit": args.lambda_head,
            "contract": {
                "query_states": len(examples_by_id),
                "transitions": len(panel_rows),
                "scoreable_rows": len(pair_rows),
                "legal_rows": int(expected["legal_rows"]),
                "over_context_rows": int(expected["over_context_rows"]),
                "cells": dict(cell_counts),
            },
            "serialization_counts": {
                "audit_pairs": int(audit["pair_count"]),
                "cell_a_pairs": int(audit["coverage"]["A"]["pair_count"]),
                "cell_d_pairs": int(audit["coverage"]["D"]["pair_count"]),
                "template0_reused_rows": int(audit["pair_count"]),
                "alternative_template_rows": len(preflight_rows),
                "new_qwen_forwards": scoreable,
                "over_context_template_rows": len(preflight_rows) - scoreable,
            },
            "serialization_runtime_projection": runtime,
            "hashes": {
                "audit_manifest": sha256_file(args.artifact_dir / "serialization_audit_manifest.json"),
                "preflight": sha256_file(args.artifact_dir / "serialization_preflight.jsonl"),
                "candidate_targets": sha256_file(args.artifact_dir / "candidate_target_rows.jsonl"),
            },
            "elapsed_seconds": time.perf_counter() - started,
        }
        atomic_write_json(args.artifact_dir / "preflight_summary.json", summary)
        atomic_write_text(args.artifact_dir / "preflight_report.md", _preflight_report(summary))
        attempt.progress(
            status=summary["status"],
            latest_validated_checkpoint=str(args.artifact_dir / "preflight_summary.json"),
            exact_new_qwen_forwards=scoreable,
            expected_h100_hours=runtime["expected_h100_hours"],
        )
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
