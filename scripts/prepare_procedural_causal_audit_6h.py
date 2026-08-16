from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
from transformers import AutoTokenizer

from rcmf.config import load_config, save_resolved_config
from rcmf.training.datasets import (
    _appworld_messages_from_example,
    load_decision_examples,
)
from rcmf.training.procedural_causal_audit_6h import (
    build_condition_manifest,
    build_signature_equivalence_manifest,
    classify_audit_states,
    messages_with_signature_card,
    signature_only_card,
)
from rcmf.training.state_conditioned_transition_6b import (
    AttemptLedger,
    initialize_or_validate_run_manifest,
)
from rcmf.training.transition_memory_6a import (
    messages_with_transition_memory,
    state_example_id,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found: {path}")
    return rows


def _atomic_write_jsonl(
    path: Path, rows: Sequence[Mapping[str, Any]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent
    ) as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _assert_count(name: str, actual: int, expected: int) -> None:
    if int(actual) != int(expected):
        raise ValueError(f"{name} differs: {actual} != {expected}")


def _example_by_state(examples: Sequence[Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for index, example in enumerate(examples):
        identity = state_example_id(index, example)
        if identity in output:
            raise ValueError(f"Duplicate decision example: {identity}")
        output[identity] = example
    return output


def _render_messages(tokenizer: Any, messages: list[dict[str, str]]) -> str:
    try:
        return tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
    except TypeError:
        return tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )


def _prompt_preflight(
    *,
    tokenizer: Any,
    examples_by_state: Mapping[str, Any],
    conditions: Sequence[Mapping[str, Any]],
    transitions_by_id: Mapping[str, Mapping[str, Any]],
    signatures_by_id: Mapping[str, Mapping[str, Any]],
    prompt_profile: str,
    context_limit: int,
    requested_new_tokens: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for condition in conditions:
        state_id = str(condition["state_example_id"])
        example = examples_by_state[state_id]
        messages = _appworld_messages_from_example(example, prompt_profile)
        prompt_kind = str(condition["prompt_kind"])
        transition_id = condition.get("transition_id")
        card_hash = None
        if prompt_kind == "raw_transition":
            messages = messages_with_transition_memory(
                messages,
                transitions_by_id[str(transition_id)],
                prompt_profile,
            )
        elif prompt_kind == "signature_card":
            card = signature_only_card(signatures_by_id[str(transition_id)])
            card_hash = __import__("hashlib").sha256(
                card.encode("utf-8")
            ).hexdigest()
            messages = messages_with_signature_card(
                messages, card, prompt_profile
            )
        elif prompt_kind != "bare":
            raise ValueError(f"Unknown prompt kind: {prompt_kind}")
        rendered = _render_messages(tokenizer, messages)
        prompt_tokens = len(
            tokenizer(
                rendered, add_special_tokens=True, truncation=False
            )["input_ids"]
        )
        remaining = context_limit - prompt_tokens
        if remaining <= 0:
            raise ValueError(
                f"Condition prompt exceeds context without generation: "
                f"{condition['condition_key']}={prompt_tokens}"
            )
        effective = min(requested_new_tokens, remaining)
        rows.append(
            {
                "condition_key": str(condition["condition_key"]),
                "state_example_id": state_id,
                "condition_name": str(condition["condition_name"]),
                "prompt_kind": prompt_kind,
                "transition_id": transition_id,
                "prompt_tokens": prompt_tokens,
                "context_limit": context_limit,
                "completion_token_headroom": remaining,
                "requested_max_new_tokens": requested_new_tokens,
                "effective_max_new_tokens": effective,
                "context_limited_completion": effective
                < requested_new_tokens,
                "prompt_sha256": __import__("hashlib").sha256(
                    rendered.encode("utf-8")
                ).hexdigest(),
                "signature_card_sha256": card_hash,
                "truncated": False,
            }
        )
    summary = {
        "condition_count": len(rows),
        "context_limited_completion_count": sum(
            bool(row["context_limited_completion"]) for row in rows
        ),
        "minimum_completion_headroom": min(
            int(row["completion_token_headroom"]) for row in rows
        ),
        "maximum_prompt_tokens": max(int(row["prompt_tokens"]) for row in rows),
        "mean_prompt_tokens": sum(int(row["prompt_tokens"]) for row in rows)
        / len(rows),
        "truncated_count": 0,
    }
    return rows, summary


def _runtime_projection(
    condition_count: int,
    replay_state_count: int,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    runtime = settings["runtime"]
    estimates: dict[str, Any] = {}
    for scenario in ("best", "expected", "conservative"):
        generation_seconds = condition_count * float(
            runtime["generation_seconds_per_condition"][scenario]
        )
        condition_replay_seconds = condition_count * float(
            runtime["replay_seconds_per_condition"][scenario]
        )
        preflight_replay_seconds = replay_state_count * float(
            runtime["replay_seconds_per_condition"][scenario]
        )
        estimates[scenario] = {
            "qwen_generation_seconds": generation_seconds,
            "h100_hours": generation_seconds / 3600.0,
            "condition_replay_seconds": condition_replay_seconds,
            "preflight_replay_seconds": preflight_replay_seconds,
            "wall_seconds": generation_seconds
            + condition_replay_seconds
            + preflight_replay_seconds,
        }
    artifact_bytes = int(runtime["artifact_bytes_per_condition"]) * condition_count
    threshold = float(runtime["review_threshold_h100_hours"])
    return {
        "condition_count": condition_count,
        "qwen_generation_count": condition_count,
        "appworld_condition_reconstruction_count": condition_count,
        "appworld_generated_action_execution_count": condition_count,
        "ground_truth_replay_validation_state_count": replay_state_count,
        "scenarios": estimates,
        "projected_artifact_bytes": artifact_bytes,
        "projected_artifact_gib": artifact_bytes / (1024**3),
        "review_threshold_h100_hours": threshold,
        "requires_explicit_runtime_approval": estimates["expected"][
            "h100_hours"
        ]
        > threshold,
        "resume_plan": {
            "unit": "one immutable state-condition key",
            "atomic_output": "one JSON file per condition with prompt/config hashes",
            "skip_policy": "skip only hash-validated completed condition outputs",
            "attempts": "append-only attempts.jsonl with parent attempt identity",
            "heartbeat": "persistent heartbeat at least every four minutes",
            "duplicate_policy": "condition_key uniqueness plus post-run duplicate validation",
        },
    }


def _report_markdown(summary: Mapping[str, Any]) -> str:
    strata = summary["audit_strata"]
    runtime = summary["runtime_projection"]
    prompt = summary["prompt_preflight"]
    lines = [
        "# EXP-024A Preflight",
        "",
        f"- Run UUID: `{summary['run_uuid']}`",
        f"- Signature classes: {summary['signature_equivalence']['signature_class_count']}",
        f"- Audit states: {strata['state_count']} across {strata['task_count']} tasks",
        f"- Strata A/B/C/D/E: {strata['stratum_state_counts']}",
        "- Primary non-documentation high-tier coverage: "
        f"{strata['primary_non_documentation_high_tier_state_count']} states / "
        f"{strata['primary_non_documentation_high_tier_task_count']} tasks",
        f"- Conditions / Qwen generations: {runtime['condition_count']}",
        f"- Context-limited completion budgets: {prompt['context_limited_completion_count']}",
        f"- Minimum completion headroom: {prompt['minimum_completion_headroom']} tokens",
        "- Prompt truncation: 0",
        "",
        "## Runtime projection",
        "",
        "| Scenario | H100 hours | Wall hours |",
        "|---|---:|---:|",
    ]
    for name in ("best", "expected", "conservative"):
        value = runtime["scenarios"][name]
        lines.append(
            f"| {name} | {value['h100_hours']:.3f} | "
            f"{value['wall_seconds'] / 3600.0:.3f} |"
        )
    lines.extend(
        [
            "",
            f"Projected artifacts: {runtime['projected_artifact_gib']:.3f} GiB.",
            "",
            "The run is resumable by atomic state-condition outputs. Existing outputs "
            "are reused only after condition, prompt, config, and manifest hashes match.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_procedural_causal_audit_6h.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp024a")
    parser.add_argument("--parent-attempt-id")
    parser.add_argument("--resume-checkpoint")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6h"]
    persistent = Path(settings["persistent_root"])
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError(f"Persistent root is not mounted: {persistent}")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)

    source = Path(settings["source_data"])
    exp017 = Path(settings["exp017_artifact"])
    exp020 = Path(settings["exp020_artifact"])
    exp021 = Path(settings["exp021_artifact"])
    exp022 = Path(settings["exp022_artifact"])
    exp023 = Path(settings["exp023_artifact"])
    paths = {
        "decision_examples": source / "decision_examples.jsonl",
        "memory_records": source / "memory_records.jsonl",
        "transition_manifest": exp017 / "transition_manifest.jsonl",
        "exp017_validation": exp017 / "postrun_validation.json",
        "exp020_teacher_cache": exp020 / "teacher_cache.jsonl",
        "expanded_query_manifest": exp020 / "expanded_query_manifest.json",
        "exp020_validation": exp020 / "postrun_validation.json",
        "exp021_validation": exp021 / "postrun_validation.json",
        "one_step_query_manifest": exp022 / "one_step_query_manifest.json",
        "exp022_validation": exp022 / "postrun_validation.json",
        "full_transition_signatures": exp023
        / "full_transition_signature_manifest.jsonl",
        "full_procedural_labels": exp023 / "full_procedural_label_rows.jsonl",
        "full_pair_preflight": exp023 / "full_pair_preflight.jsonl",
        "one_step_pair_preflight": exp023 / "one_step_pair_preflight.jsonl",
        "exp023_validation": exp023 / "postrun_validation.json",
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
            "signature_equivalence_classes",
            "immutable_45_state_strata",
            "all_conditions_frozen_before_generation",
            "tokenizer_only_prompt_preflight",
            "runtime_storage_projection",
            "no_qwen_forward",
            "no_appworld_instance",
        ],
    )
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    save_resolved_config(cfg, args.artifact_dir / "resolved_config.yaml")
    atomic_write_json(args.artifact_dir / "stage_c_6h_settings.json", settings)

    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="signature_condition_and_cost_preflight",
        command=[str(value) for value in __import__("sys").argv],
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
        prior_validations = {
            name: _load_json(paths[name])
            for name in (
                "exp017_validation",
                "exp020_validation",
                "exp021_validation",
                "exp022_validation",
                "exp023_validation",
            )
        }
        if not all(bool(value.get("passed")) for value in prior_validations.values()):
            raise ValueError("One or more immutable prior artifacts failed validation")

        expected = settings["expected"]
        examples = load_decision_examples(paths["decision_examples"])
        examples_by_state = _example_by_state(examples)
        transitions = _load_rows(paths["transition_manifest"])
        signatures = _load_rows(paths["full_transition_signatures"])
        labels = _load_rows(paths["full_procedural_labels"])
        full_preflight = _load_rows(paths["full_pair_preflight"])
        one_step_preflight = _load_rows(paths["one_step_pair_preflight"])
        one_step_manifest = _load_json(paths["one_step_query_manifest"])
        expanded_query_manifest = _load_json(paths["expanded_query_manifest"])
        audit_rows = list(one_step_manifest["rows"])

        _assert_count("decision examples", len(examples), expected["decision_examples"])
        _assert_count("transitions", len(transitions), expected["transitions"])
        _assert_count("unique signatures", len({row['action_signature']['signature_sha256'] for row in signatures}), expected["unique_signatures"])
        _assert_count(
            "query states",
            int(expanded_query_manifest["query_count"]),
            expected["query_states"],
        )
        _assert_count("one-step states", len(audit_rows), expected["one_step_audit_states"])
        _assert_count("full legal pairs", len(full_preflight), expected["full_legal_pairs"])
        _assert_count("full scoreable pairs", sum(not bool(row['over_context']) for row in full_preflight), expected["full_scoreable_pairs"])
        _assert_count("one-step scoreable pairs", sum(not bool(row['over_context']) for row in one_step_preflight), expected["one_step_scoreable_pairs"])
        if len(labels) != len(full_preflight):
            raise ValueError("Procedural labels and legal preflight rows differ")

        equivalence = build_signature_equivalence_manifest(transitions, signatures)
        _assert_count("equivalence classes", equivalence["signature_class_count"], expected["unique_signatures"])
        _assert_count("duplicate transitions", equivalence["duplicate_transition_count"], expected["duplicate_transitions"])
        _assert_count("API-documentation transitions", equivalence["api_documentation_transition_count"], expected["api_documentation_transitions"])
        atomic_write_json(args.artifact_dir / "signature_equivalence_manifest.json", equivalence)

        audit_ids = {str(row["state_example_id"]) for row in audit_rows}
        audit_labels = [
            row
            for row in labels
            if str(row["state_example_id"]) in audit_ids
        ]
        scoreable_audit_labels = [
            row for row in audit_labels if bool(row["scoreable_under_context"])
        ]
        strata = classify_audit_states(audit_rows, scoreable_audit_labels)
        atomic_write_json(args.artifact_dir / "audit_state_strata.json", strata)
        if (
            int(strata["primary_non_documentation_high_tier_state_count"])
            < int(settings["selection"]["minimum_primary_states"])
            or int(strata["primary_non_documentation_high_tier_task_count"])
            < int(settings["selection"]["minimum_primary_tasks"])
        ):
            raise RuntimeError(
                "Primary non-documentation Tier-3/4 audit coverage gate failed"
            )

        condition_manifest = build_condition_manifest(
            strata, audit_labels, equivalence
        )
        atomic_write_json(args.artifact_dir / "condition_manifest.json", condition_manifest)
        transitions_by_id = {
            str(row["transition_id"]): row for row in transitions
        }
        signatures_by_id = {
            str(row["transition_id"]): row for row in signatures
        }
        tokenizer = AutoTokenizer.from_pretrained(
            str(settings["generation"]["model_name"]),
            trust_remote_code=True,
        )
        prompt_rows, prompt_summary = _prompt_preflight(
            tokenizer=tokenizer,
            examples_by_state=examples_by_state,
            conditions=condition_manifest["conditions"],
            transitions_by_id=transitions_by_id,
            signatures_by_id=signatures_by_id,
            prompt_profile=str(settings["generation"]["prompt_profile"]),
            context_limit=int(settings["generation"]["context_limit"]),
            requested_new_tokens=int(settings["generation"]["max_new_tokens"]),
        )
        _atomic_write_jsonl(
            args.artifact_dir / "condition_prompt_preflight.jsonl", prompt_rows
        )
        runtime = _runtime_projection(
            len(condition_manifest["conditions"]), len(audit_rows), settings
        )
        summary = {
            "format": "procedural_causal_preflight_summary_6h_v1",
            "run_uuid": str(settings["run_uuid"]),
            "source_commit": args.lambda_head,
            "immutable_data_hashes": data_hashes,
            "signature_equivalence": {
                key: equivalence[key]
                for key in (
                    "transition_count",
                    "signature_class_count",
                    "duplicate_transition_count",
                    "duplicate_class_count",
                    "api_documentation_transition_count",
                    "class_size_distribution",
                    "class_size_summary",
                    "manifest_sha256",
                )
            },
            "audit_strata": {
                key: strata[key]
                for key in (
                    "state_count",
                    "task_count",
                    "stratum_state_counts",
                    "stratum_task_counts",
                    "primary_non_documentation_high_tier_state_count",
                    "primary_non_documentation_high_tier_task_count",
                    "manifest_sha256",
                )
            },
            "condition_manifest": {
                key: condition_manifest[key]
                for key in (
                    "condition_count",
                    "condition_counts",
                    "prompt_kind_counts",
                    "missing_condition_count",
                    "missing_conditions",
                    "manifest_sha256",
                )
            },
            "prompt_preflight": prompt_summary,
            "runtime_projection": runtime,
            "elapsed_seconds": time.perf_counter() - started,
            "gpu_forward_count": 0,
            "appworld_instance_count": 0,
            "truncated_prompt_count": 0,
        }
        atomic_write_json(args.artifact_dir / "preflight_summary.json", summary)
        atomic_write_text(
            args.artifact_dir / "preflight_report.md", _report_markdown(summary)
        )
        attempt.progress(
            phase="signature_condition_and_cost_preflight_complete",
            completed_conditions=len(condition_manifest["conditions"]),
            projected_h100_hours=runtime["scenarios"]["expected"]["h100_hours"],
            requires_approval=runtime["requires_explicit_runtime_approval"],
            latest_validated_checkpoint=str(
                args.artifact_dir / "preflight_summary.json"
            ),
        )
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
