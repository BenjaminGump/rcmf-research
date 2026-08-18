from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
import torch
from transformers import AutoTokenizer

from rcmf.benchmarks.appworld.prompt import appworld_renderer_metadata
from rcmf.config import load_config
from rcmf.training.datasets import (
    _appworld_messages_from_example,
    _render_prompt_with_metadata,
    _target_suffix,
    load_decision_examples,
)
from rcmf.training.state_conditioned_program_7d import (
    assert_program_student_contract,
    build_frozen_cell_pairs,
    build_program_training_pairs,
    estimate_qwen_runtime,
    frozen_pair_context_status,
    grouped_decoder_pair_split,
    projected_program_parameter_counts,
    selector_candidate_projection,
    weighted_field_algebra_validation,
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
    sha256_text,
    write_jsonl,
)
from scripts.prepare_signature_balanced_field_7c import _query_signatures, _task_split
from scripts.run_raw_text_teacher_pilot import _target_token_ids


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        str(row["attempt_id"])
        for row in read_jsonl(path)
        if row.get("event") == "start"
    }


def _load_inputs(settings: Mapping[str, Any]) -> dict[str, Path]:
    parent_b = Path(str(settings["parent_exp025b"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    parent_cr = Path(str(settings["parent_exp025cr"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    return {
        "replay_manifest": parent_b / "replay_validated_corpus_manifest.json",
        "corpus_summary": corpus / "summary.json",
        "structural_validation": corpus / "structural_validation.json",
        "decisions": corpus / "decision_examples.jsonl",
        "transitions": parent_b
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        "parent_split": parent_b
        / "clean_procedural_audit/clean_parent_split_manifest.json",
        "transition_signatures": parent_b
        / "clean_procedural_audit/clean_transition_signature_manifest.jsonl",
        "signature_classes": parent_b
        / "clean_procedural_audit/clean_signature_equivalence_manifest.json",
        "labels": parent_c / "clean_full_procedural_labels.jsonl",
        "candidate_spaces": parent_c / "candidate_space_manifest.json",
        "state_multiview": parent_c
        / "representation_cache/multiview/state_multiview.pt",
        "transition_multiview": parent_c
        / "representation_cache/multiview/transition_multiview.pt",
        "selector_ensemble": parent_c / "selector/ensemble_scores.pt",
        "selector_summary": parent_c / "selector/selector_summary.json",
        "selector_behavior_summary": parent_cr / "final_exp025cr_summary.json",
        "selector_b_diagnostics": parent_c
        / "selector/evaluation/B_selected_transition_diagnostics.jsonl",
        "selector_d_diagnostics": parent_c
        / "selector/evaluation/D_selected_transition_diagnostics.jsonl",
        "selector_e_diagnostics": parent_c
        / "selector/evaluation/E_selected_transition_diagnostics.jsonl",
        "exp017_pair_response": Path(
            "runs/stage_c/transition_memory_6a_20260814_001/"
            "pair_oracle_response_cache/response_cache.jsonl"
        ),
        "exp017_static_response": Path(
            "runs/stage_c/transition_memory_6a_20260814_001/"
            "static_transition_response_cache/response_cache.jsonl"
        ),
        "clean_scalar_transition_teacher": parent_b
        / "clean_cache_rebuild/transition_teacher/teacher_cache.jsonl",
    }


def _validate_immutable_inputs(
    *, settings: Mapping[str, Any], paths: Mapping[str, Path]
) -> dict[str, Any]:
    missing = {name: str(path) for name, path in paths.items() if not path.exists()}
    if missing:
        raise FileNotFoundError(f"Missing immutable EXP-025D inputs: {missing}")
    replay = _json(paths["replay_manifest"])
    ensemble_sha = sha256_file(paths["selector_ensemble"])
    selector = _json(paths["selector_summary"])
    selector_behavior = _json(paths["selector_behavior_summary"])
    parent_split = _json(paths["parent_split"])
    checks = {
        "structural_lineage": str(replay["structural_corpus_lineage_sha256"])
        == str(settings["expected_structural_lineage_sha256"]),
        "replay_lineage": str(replay["lineage_sha256"])
        == str(settings["expected_replay_lineage_sha256"]),
        "selector_ensemble_sha256": ensemble_sha
        == str(settings["expected_selector_ensemble_sha256"]),
        "selector_summary_sha256": str(selector["ensemble"]["sha256"])
        == ensemble_sha,
        "strict_b_gate": bool(selector["gates"]["strict_b"]["passed"]),
        "deployment_e_gate": bool(selector["gates"]["deployment_e"]["passed"]),
        "heldout_parent_d_gate": bool(
            selector["gates"]["heldout_parent_d"]["passed"]
        ),
        "behavioral_selector_branch": str(
            selector_behavior["decision"]["decision_branch"]
        )
        == "signature_balanced_field_selector_behaviorally_validated",
        "behavioral_selector_validated": bool(
            selector_behavior["decision"][
                "automatic_field_selection_behaviorally_validated"
            ]
        ),
        "train_parent_count": int(parent_split["train_parent_count"])
        == int(settings["expected"]["train_parents"]),
        "heldout_parent_count": int(parent_split["heldout_parent_count"])
        == int(settings["expected"]["heldout_parents"]),
    }
    if not all(checks.values()):
        raise ValueError(
            f"Immutable selector/lineage validation failed: "
            f"{[name for name, passed in checks.items() if not passed]}"
        )
    return {"checks": checks, "selector_ensemble_sha256": ensemble_sha}


def _context_builder(
    *, tokenizer: Any, examples: Sequence[Any], prompt_profile: str
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    contexts: dict[str, dict[str, Any]] = {}
    index_by_state: dict[str, int] = {}
    for index, example in enumerate(examples):
        state_id = state_example_id(index, example)
        messages = _appworld_messages_from_example(example, prompt_profile)
        prompt, prompt_metadata = _render_prompt_with_metadata(
            tokenizer, messages, prompt_profile
        )
        prompt_ids = tokenizer.encode(prompt, add_special_tokens=False)
        target_ids = _target_token_ids(tokenizer, example)
        contexts[state_id] = {
            "example": example,
            "base_messages": messages,
            "base_prompt": prompt,
            "prompt_metadata": prompt_metadata,
            "prompt_tokens": len(prompt_ids),
            "target_ids": target_ids,
            "target_text": _target_suffix(example),
            "target_sha256": sha256_text(_target_suffix(example)),
            "target_token_sha256": sha256_text(
                ",".join(str(value) for value in target_ids)
            ),
        }
        index_by_state[state_id] = index
    return contexts, index_by_state


def _preflight_pair(
    *,
    row: Mapping[str, Any],
    tokenizer: Any,
    contexts: Mapping[str, Mapping[str, Any]],
    transitions: Mapping[str, Mapping[str, Any]],
    prompt_profile: str,
    context_limit: int,
    cache: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    state_id = str(row["state_example_id"])
    transition_id = str(row["transition_id"])
    key = (state_id, transition_id)
    if key not in cache:
        context = contexts[state_id]
        transition = transitions[transition_id]
        messages = messages_with_transition_memory(
            context["base_messages"], transition, prompt_profile
        )
        teacher_prompt, _ = _render_prompt_with_metadata(
            tokenizer, messages, prompt_profile
        )
        teacher_tokens = len(
            tokenizer.encode(teacher_prompt, add_special_tokens=False)
        )
        target_tokens = len(context["target_ids"])
        cache[key] = {
            "prompt_tokens": int(context["prompt_tokens"]),
            "teacher_prompt_tokens": teacher_tokens,
            "target_tokens": target_tokens,
            "teacher_total_tokens_with_target": teacher_tokens + target_tokens,
            "context_limit": int(context_limit),
            "over_context": teacher_tokens + target_tokens > int(context_limit),
            "prompt_sha256": sha256_text(str(context["base_prompt"])),
            "teacher_prompt_sha256": sha256_text(teacher_prompt),
            "target_sha256": str(context["target_sha256"]),
            "target_token_sha256": str(context["target_token_sha256"]),
            "transition_content_sha256": str(
                transition["transition_content_sha256"]
            ),
        }
    return {**dict(row), **cache[key]}


def _context_preflight_manifests(
    *,
    manifests: Mapping[str, Mapping[str, Any]],
    tokenizer: Any,
    contexts: Mapping[str, Mapping[str, Any]],
    transitions: Mapping[str, Mapping[str, Any]],
    prompt_profile: str,
    context_limit: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    cache: dict[tuple[str, str], dict[str, Any]] = {}
    output: dict[str, list[dict[str, Any]]] = {}
    for name, manifest in manifests.items():
        rows = []
        for source in manifest["pairs"]:
            row = dict(source)
            preflight = frozen_pair_context_status(
                _preflight_pair(
                    row=row,
                    tokenizer=tokenizer,
                    contexts=contexts,
                    transitions=transitions,
                    prompt_profile=prompt_profile,
                    context_limit=context_limit,
                    cache=cache,
                )
            )
            rows.append(preflight)
        output[name] = rows
    all_rows = [row for rows in output.values() for row in rows]
    report = {
        "logical_pair_count": len(all_rows),
        "unique_pair_count": len({str(row["pair_id"]) for row in all_rows}),
        "scoreable_pair_count": sum(not row["over_context"] for row in all_rows),
        "over_context_pair_count": sum(row["over_context"] for row in all_rows),
        "same_class_substitution_count": 0,
        "truncation_count": 0,
        "cross_class_substitution_count": 0,
        "by_manifest": {
            name: {
                "logical": len(rows),
                "scoreable": sum(not row["over_context"] for row in rows),
                "over_context": sum(row["over_context"] for row in rows),
            }
            for name, rows in output.items()
        },
    }
    return output, report


def _response_cache_reuse(
    *,
    rows: Sequence[Mapping[str, Any]],
    paths: Mapping[str, Path],
    renderer_version: str,
    clean_lineage_sha256: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    prior = []
    for name in ("exp017_pair_response", "exp017_static_response"):
        prior.extend(_rows(paths[name]))
    prior_by_pair: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in prior:
        prior_by_pair[str(row["pair_id"])].append(row)
    reusable = {}
    rejection_counts: Counter[str] = Counter()
    for row in rows:
        pair_id = str(row["pair_id"])
        for candidate in prior_by_pair.get(pair_id, []):
            checks = {
                "prompt": str(candidate.get("prompt_sha256")) == str(row["prompt_sha256"]),
                "teacher_prompt": str(candidate.get("teacher_prompt_sha256"))
                == str(row["teacher_prompt_sha256"]),
                "target": str(candidate.get("target_sha256")) == str(row["target_sha256"]),
                "target_tokens": str(candidate.get("target_token_sha256"))
                == str(row["target_token_sha256"]),
                "transition_content": str(candidate.get("transition_content_sha256"))
                == str(row["transition_content_sha256"]),
                "model": str(candidate.get("model_name")) == "Qwen/Qwen3-8B",
                "renderer": str(candidate.get("renderer_version")) == str(renderer_version),
                "clean_lineage": str(candidate.get("clean_lineage_sha256"))
                == str(clean_lineage_sha256),
                "top64": bool(candidate.get("target_positions")),
                "student_bare": not bool(candidate.get("student_prompt_contains_raw_memory")),
            }
            if all(checks.values()):
                reusable[pair_id] = dict(candidate)
                break
            rejection_counts.update(name for name, passed in checks.items() if not passed)
    scalar_pair_ids = {
        str(row["pair_id"])
        for row in _rows(paths["clean_scalar_transition_teacher"])
        if bool(row.get("valid_for_loss"))
    }
    pair_ids = {str(row["pair_id"]) for row in rows}
    report = {
        "selected_unique_scoreable_pairs": len(pair_ids),
        "complete_top64_reusable_rows": len(reusable),
        "new_top64_rows": len(pair_ids - set(reusable)),
        "scalar_clean_teacher_overlap": len(pair_ids & scalar_pair_ids),
        "scalar_rows_not_sufficient_for_sparse_teacher": True,
        "reuse_rejection_counts": dict(sorted(rejection_counts.items())),
    }
    return reusable, report


def _coverage(
    rows: Sequence[Mapping[str, Any]],
    *,
    labels_by_pair: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    labels = [labels_by_pair[str(row["pair_id"])] for row in rows]
    state_counts = Counter(str(row["state_example_id"]) for row in rows)
    return {
        "pair_count": len(rows),
        "state_count": len({str(row["state_example_id"]) for row in rows}),
        "task_count": len({str(row["state_task_id"]) for row in rows}),
        "transition_count": len({str(row["transition_id"]) for row in rows}),
        "parent_count": len({str(row["transition_parent_id"]) for row in rows}),
        "signature_class_count": len(
            {str(row["signature_class_id"]) for row in rows}
        ),
        "role_counts": dict(sorted(Counter(str(row["pair_role"]) for row in rows).items())),
        "procedural_tier_counts": dict(
            sorted(Counter(int(row["procedural_tier"]) for row in labels).items())
        ),
        "exact_api_pair_count": sum(
            bool(row["exact_api_sequence"]) for row in labels
        ),
        "query_apps": dict(
            sorted(Counter(str(row["query_primary_app"]) for row in labels).items())
        ),
        "query_action_types": dict(
            sorted(
                Counter(str(row["query_coarse_action_type"]) for row in labels).items()
            )
        ),
        "transition_apps": dict(
            sorted(
                Counter(str(row["transition_primary_app"]) for row in labels).items()
            )
        ),
        "transition_action_types": dict(
            sorted(
                Counter(
                    str(row["transition_coarse_action_type"]) for row in labels
                ).items()
            )
        ),
        "states_with_multiple_pairs": sum(count > 1 for count in state_counts.values()),
        "within_state_pair_comparison_count": sum(
            count * (count - 1) // 2 for count in state_counts.values()
        ),
    }


def _frozen_selection_comparison(
    rows: Sequence[Mapping[str, Any]],
    diagnostics: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    expected = {
        str(row["state_example_id"]): row
        for row in diagnostics
    }
    mismatches = []
    for row in rows:
        state_id = str(row["state_example_id"])
        source = expected[state_id]
        if (
            str(row["signature_class_id"]) != str(source["top1_class_id"])
            or str(row["transition_id"]) != str(source["selected_transition_id"])
        ):
            mismatches.append(
                {
                    "state_example_id": state_id,
                    "expected_class_id": str(source["top1_class_id"]),
                    "actual_class_id": str(row["signature_class_id"]),
                    "expected_transition_id": str(source["selected_transition_id"]),
                    "actual_transition_id": str(row["transition_id"]),
                }
            )
    return {
        "pair_count": len(rows),
        "exact_match_count": len(rows) - len(mismatches),
        "mismatch_count": len(mismatches),
        "passed": not mismatches,
        "mismatches": mismatches,
    }


def _runtime_projection(
    *, settings: Mapping[str, Any], counts: Mapping[str, int], reuse: Mapping[str, Any]
) -> dict[str, Any]:
    decoder = settings["decoder"]
    program = settings["program"]
    a_count = int(counts["A"])
    calibration = int(counts["decoder_calibration"])
    inversion = int(counts["decoder_heldout"])
    decoder_minimum = (calibration + inversion) * int(
        decoder["calibration_updates"][-1]
    )
    decoder_maximum = (calibration + inversion) * int(decoder["optional_updates"])
    architectures = len(program["trainable_architectures"])
    tiny = int(program["tiny_pair_count"]) * int(program["tiny_updates_per_pair"])
    smoke = int(program["smoke_pair_count"]) * int(program["smoke_updates_per_pair"])
    full_updates = int(program["checkpoint_updates_per_pair"][-1])
    optional_updates = int(program["optional_updates_per_pair"])
    program_minimum = tiny + smoke + architectures * a_count * full_updates
    additional_primary_seeds = len(program["final_seeds"]) - 1
    program_expected = program_minimum + additional_primary_seeds * a_count * full_updates
    program_maximum = (
        tiny + smoke + (architectures + additional_primary_seeds) * a_count * optional_updates
    )
    heldout = sum(int(counts[name]) for name in ("B", "C", "D", "E"))
    eval_best = (calibration + inversion) * 4 + architectures * (3 * a_count + heldout)
    eval_expected = eval_best + additional_primary_seeds * (3 * a_count + heldout)
    eval_conservative = (calibration + inversion) * 5 + (
        architectures + additional_primary_seeds
    ) * (4 * a_count + heldout)
    runtime = estimate_qwen_runtime(
        new_teacher_rows=int(reuse["new_top64_rows"]),
        unique_teacher_states=int(counts["unique_states"]),
        decoder_minimum_updates=decoder_minimum,
        decoder_maximum_updates=decoder_maximum,
        program_minimum_updates=program_minimum,
        program_expected_updates=program_expected,
        program_maximum_updates=program_maximum,
        seconds_per_teacher_forward=settings["teacher_cache"]["seconds_per_forward"],
        seconds_per_backward_update=settings["runtime"]["seconds_per_backward_update"],
        evaluation_forward_count={
            "best": eval_best,
            "expected": eval_expected,
            "conservative": eval_conservative,
        },
    )
    artifact_bytes = (
        (int(reuse["new_top64_rows"]) + int(reuse["complete_top64_reusable_rows"]))
        * int(settings["teacher_cache"]["artifact_bytes_per_row"])
        + (architectures + additional_primary_seeds)
        * 5
        * int(settings["runtime"]["checkpoint_bytes_per_model"])
    )
    report_hours = settings["runtime"]["report_validation_hours"]
    for scenario, values in runtime["scenarios"].items():
        values["report_validation_hours"] = float(report_hours[scenario])
        values["wall_hours_including_reports"] = (
            float(values["h100_hours"]) + float(report_hours[scenario])
        )
    threshold = float(settings["runtime"]["review_threshold_h100_hours"])
    return {
        **runtime,
        "decoder_updates": {
            "minimum": decoder_minimum,
            "maximum": decoder_maximum,
        },
        "program_updates": {
            "minimum": program_minimum,
            "expected": program_expected,
            "maximum": program_maximum,
            "architecture_count": architectures,
            "tiny": tiny,
            "smoke": smoke,
        },
        "evaluation_forwards": {
            "best": eval_best,
            "expected": eval_expected,
            "conservative": eval_conservative,
        },
        "projected_artifact_bytes": artifact_bytes,
        "review_threshold_h100_hours": threshold,
        "requires_explicit_runtime_approval": float(
            runtime["scenarios"]["expected"]["h100_hours"]
        )
        > threshold,
        "historical_rate_provenance": {
            "exp016a_ratio1_u48_to_u64_seconds": 5683.34,
            "exp016a_updates_in_interval": 3072,
            "observed_seconds_per_pair_update": 5683.34 / 3072.0,
        },
    }


def _report(summary: Mapping[str, Any]) -> str:
    runtime = summary["runtime_projection"]
    lines = [
        "# EXP-025D CPU Pair and Runtime Preflight",
        "",
        "## Immutable Inputs",
        "",
        f"- selector ensemble: `{summary['immutable_validation']['selector_ensemble_sha256']}`",
        f"- structural lineage: `{summary['lineages']['structural']}`",
        f"- replay lineage: `{summary['lineages']['replay']}`",
        "",
        "## Exact Pairs",
        "",
    ]
    for name, values in summary["pair_coverage"].items():
        lines.append(
            f"- {name}: `{values['pair_count']}` pairs, `{values['state_count']}` states, "
            f"`{values['task_count']}` tasks, `{values['parent_count']}` parents."
        )
    cache = summary["teacher_cache_reuse"]
    lines.extend(
        [
            "",
            "## Teacher Cache",
            "",
            f"- unique scoreable pairs: `{cache['selected_unique_scoreable_pairs']}`",
            f"- complete top64 reused/new: `{cache['complete_top64_reusable_rows']}` / "
            f"`{cache['new_top64_rows']}`",
            f"- scalar clean-row overlap: `{cache['scalar_clean_teacher_overlap']}` "
            "(not sufficient for sparse-teacher distillation)",
            "",
            "## Runtime Review",
            "",
        ]
    )
    for name in ("best", "expected", "conservative"):
        row = runtime["scenarios"][name]
        lines.append(
            f"- {name}: `{row['h100_hours']:.2f}` H100 hours optimization/scoring; "
            f"`{row['wall_hours_including_reports']:.2f}` wall hours including final "
            "validation/reporting."
        )
    lines.extend(
        [
            f"- projected artifact bytes: `{runtime['projected_artifact_bytes']}`",
            f"- exceeds 12-H100-hour review threshold: "
            f"`{runtime['requires_explicit_runtime_approval']}`",
            "",
            "No Qwen model was loaded and no GPU work was launched by this preflight.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_state_conditioned_program_7d.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", required=True)
    parser.add_argument("--tmux-session", default="exp025d")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7d"]
    if os.name != "nt" and not os.path.ismount(Path(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    paths = _load_inputs(settings)
    immutable = _validate_immutable_inputs(settings=settings, paths=paths)
    source_hashes = {name: sha256_file(path) for name, path in paths.items()}
    config_sha = sha256_file(args.config)
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="cpu_pair_manifest_and_runtime_preflight",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_sha,
        data_manifest_hashes=source_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        initialize_or_validate_run_manifest(
            args.artifact_dir / "run_manifest.json",
            run_uuid=str(settings["run_uuid"]),
            config_sha256=config_sha,
            data_manifest_hashes=source_hashes,
            source_commit=args.lambda_head,
            command_scope=[
                "immutable input validation",
                "deterministic pair manifest",
                "tokenizer-only context preflight",
                "teacher-cache reuse accounting",
                "runtime/storage projection",
                "no Qwen model load or forward",
            ],
        )
        examples = load_decision_examples(paths["decisions"])
        task_split = _task_split(Path(settings["reconciled_corpus_dir"]))
        query_rows, _ = _query_signatures(examples, task_split)
        train_transitions = [
            row
            for row in _rows(paths["transitions"])
            if task_split[str(row["parent_task_id"])] == "train"
        ]
        transitions = {str(row["transition_id"]): row for row in train_transitions}
        transition_token_counts = {
            transition_id: int(row["teacher_section_tokens"])
            for transition_id, row in transitions.items()
        }
        labels = _rows(paths["labels"])
        labels_by_pair = {str(row["pair_id"]): row for row in labels}
        if len(labels_by_pair) != len(labels):
            raise ValueError("Clean procedural labels contain duplicate pair IDs")
        by_cell = {
            cell: [row for row in labels if str(row["cell"]) == cell]
            for cell in ("A", "B", "C", "D")
        }
        candidates_by_cell = {
            cell: selector_candidate_projection(rows)
            for cell, rows in by_cell.items()
        }
        class_manifest = _json(paths["signature_classes"])
        classes = {
            str(row["signature_class_id"]): row
            for row in class_manifest["classes"]
        }
        ensemble = torch.load(
            paths["selector_ensemble"], map_location="cpu", weights_only=False
        )
        scores = ensemble["scores"].to(torch.float32)
        ordered_state_ids = [str(value) for value in ensemble["ordered_state_ids"]]
        ordered_transition_ids = [
            str(value) for value in ensemble["ordered_transition_ids"]
        ]
        expected = settings["expected"]
        count_checks = {
            "train_states": sum(row["split"] == "train" for row in query_rows)
            == int(expected["train_decisions"]),
            "validation_states": sum(
                row["split"] == "validation" for row in query_rows
            )
            == int(expected["validation_decisions"]),
            "train_transitions": len(transitions) == int(expected["train_transitions"]),
            "transition_parents": len(
                {str(row["parent_memory_id"]) for row in train_transitions}
            )
            == int(expected["transition_parents"]),
            "signature_classes": len(classes) == int(expected["signature_classes"]),
            "state_score_order": set(ordered_state_ids)
            == {str(row["state_example_id"]) for row in query_rows},
            "transition_score_order": set(ordered_transition_ids) == set(transitions),
        }
        if not all(count_checks.values()):
            raise ValueError(f"Clean count contract failed: {count_checks}")
        pair_cfg = settings["pair_manifest"]
        logical = {
            "A": build_program_training_pairs(
                labels_a=by_cell["A"],
                deployment_candidate_rows=candidates_by_cell["C"],
                scores=scores,
                ordered_state_ids=ordered_state_ids,
                ordered_transition_ids=ordered_transition_ids,
                transition_token_counts=transition_token_counts,
                classes=classes,
                target_size=int(pair_cfg["program_a_target_pairs"]),
                maximum_size=int(pair_cfg["program_a_maximum_pairs"]),
                seed=int(pair_cfg["seed"]),
            ),
            "B": build_frozen_cell_pairs(
                candidate_rows=candidates_by_cell["B"],
                scores=scores,
                ordered_state_ids=ordered_state_ids,
                ordered_transition_ids=ordered_transition_ids,
                transition_token_counts=transition_token_counts,
                classes=classes,
                state_count=None,
                cell="B",
                seed=int(pair_cfg["seed"]),
            ),
            "C": build_frozen_cell_pairs(
                candidate_rows=candidates_by_cell["C"],
                scores=scores,
                ordered_state_ids=ordered_state_ids,
                ordered_transition_ids=ordered_transition_ids,
                transition_token_counts=transition_token_counts,
                classes=classes,
                state_count=int(pair_cfg["c_pairs"]),
                cell="C",
                seed=int(pair_cfg["seed"]),
            ),
            "D": build_frozen_cell_pairs(
                candidate_rows=candidates_by_cell["D"],
                scores=scores,
                ordered_state_ids=ordered_state_ids,
                ordered_transition_ids=ordered_transition_ids,
                transition_token_counts=transition_token_counts,
                classes=classes,
                state_count=int(pair_cfg["d_pairs"]),
                cell="D",
                seed=int(pair_cfg["seed"]),
            ),
            "E": build_frozen_cell_pairs(
                candidate_rows=[
                    *candidates_by_cell["B"],
                    *candidates_by_cell["D"],
                ],
                scores=scores,
                ordered_state_ids=ordered_state_ids,
                ordered_transition_ids=ordered_transition_ids,
                transition_token_counts=transition_token_counts,
                classes=classes,
                state_count=None,
                cell="E",
                seed=int(pair_cfg["seed"]),
            ),
        }
        attempt.progress(status="tokenizer_context_preflight")
        tokenizer = AutoTokenizer.from_pretrained(
            str(settings["teacher_cache"]["model_name"]),
            use_fast=True,
            local_files_only=True,
        )
        contexts, _ = _context_builder(
            tokenizer=tokenizer, examples=examples, prompt_profile=cfg.benchmark.prompt_profile
        )
        preflighted, context_report = _context_preflight_manifests(
            manifests=logical,
            tokenizer=tokenizer,
            contexts=contexts,
            transitions=transitions,
            prompt_profile=cfg.benchmark.prompt_profile,
            context_limit=int(settings["teacher_cache"]["context_limit"]),
        )
        scoreable = {
            name: [row for row in rows if not bool(row["over_context"])]
            for name, rows in preflighted.items()
        }
        frozen_selection_validation = {
            cell: _frozen_selection_comparison(
                preflighted[cell],
                _rows(paths[f"selector_{cell.lower()}_diagnostics"]),
            )
            for cell in ("B", "D", "E")
        }
        if not all(
            report["passed"] for report in frozen_selection_validation.values()
        ):
            raise ValueError(
                "Frozen selector selections differ from EXP-025C diagnostics"
            )
        minimums = {
            "A": 384,
            "B": int(pair_cfg["minimum_b_pairs"]),
            "C": int(pair_cfg["minimum_c_pairs"]),
            "D": int(pair_cfg["minimum_d_pairs"]),
        }
        failures = {
            name: (len(scoreable[name]), minimum)
            for name, minimum in minimums.items()
            if len(scoreable[name]) < minimum
        }
        if failures:
            raise ValueError(f"Scoreable pair minimum failed: {failures}")
        decoder_split = grouped_decoder_pair_split(
            scoreable["A"],
            calibration_count=int(pair_cfg["decoder_calibration_pairs"]),
            heldout_count=int(pair_cfg["decoder_heldout_pairs"]),
            seed=int(pair_cfg["seed"]) + 1,
        )
        all_scoreable_by_id = {
            str(row["pair_id"]): row
            for rows in scoreable.values()
            for row in rows
        }
        renderer_version = str(
            appworld_renderer_metadata(
                cfg.benchmark.prompt_profile, add_generation_prompt=True
            )["renderer_version"]
        )
        _, reuse = _response_cache_reuse(
            rows=list(all_scoreable_by_id.values()),
            paths=paths,
            renderer_version=renderer_version,
            clean_lineage_sha256=str(
                settings["expected_structural_lineage_sha256"]
            ),
        )
        input_contract = assert_program_student_contract(
            [
                {
                    "pair_id": row["pair_id"],
                    "state_representation_id": row["state_example_id"],
                    "transition_representation_id": row["transition_id"],
                }
                for row in scoreable["A"]
            ]
        )
        if not input_contract["passed"]:
            raise ValueError("Program student-input contract failed")
        field_algebra = weighted_field_algebra_validation(
            seed=int(pair_cfg["seed"]) + 2
        )
        if not field_algebra["passed"]:
            raise RuntimeError("Weighted field algebra validation failed")
        counts = {
            **{name: len(rows) for name, rows in scoreable.items()},
            "decoder_calibration": decoder_split["calibration_pair_count"],
            "decoder_heldout": decoder_split["heldout_pair_count"],
            "unique_states": len(
                {str(row["state_example_id"]) for row in all_scoreable_by_id.values()}
            ),
        }
        parameter_counts = projected_program_parameter_counts(
            representation_dim=int(settings["program"]["flattened_representation_dim"]),
            hidden_dim=int(settings["program"]["hidden_dim"]),
            program_dim=int(settings["program"]["program_dim"]),
            model_dim=int(settings["decoder"]["model_dim"]),
            controller_ranks=settings["program"]["controller_ranks"],
            train_parent_transition_count=len(
                {
                    str(row["transition_id"])
                    for row in by_cell["A"]
                }
            ),
        )
        runtime = _runtime_projection(settings=settings, counts=counts, reuse=reuse)
        output_root = args.artifact_dir / "preflight"
        output_root.mkdir(parents=True, exist_ok=True)
        for name, rows in preflighted.items():
            write_jsonl(output_root / f"pairs_{name}.jsonl", rows)
        write_jsonl(
            output_root / "teacher_cache_unique_scoreable_pairs.jsonl",
            sorted(all_scoreable_by_id.values(), key=lambda row: str(row["pair_id"])),
        )
        atomic_write_json(output_root / "decoder_pair_split.json", decoder_split)
        atomic_write_json(output_root / "context_preflight.json", context_report)
        atomic_write_json(output_root / "field_algebra_validation.json", field_algebra)
        pair_coverage = {
            name: _coverage(rows, labels_by_pair=labels_by_pair)
            for name, rows in scoreable.items()
        }
        pair_coverage["decoder_calibration"] = _coverage(
            decoder_split["calibration_pairs"], labels_by_pair=labels_by_pair
        )
        pair_coverage["decoder_heldout"] = _coverage(
            decoder_split["heldout_pairs"], labels_by_pair=labels_by_pair
        )
        summary = {
            "format": "state_conditioned_program_preflight_7d_v1",
            "status": "completed_runtime_review_required"
            if runtime["requires_explicit_runtime_approval"]
            else "completed_ready_for_qwen",
            "run_uuid": str(settings["run_uuid"]),
            "source_commit": args.lambda_head,
            "immutable_validation": immutable,
            "count_checks": count_checks,
            "lineages": {
                "structural": str(settings["expected_structural_lineage_sha256"]),
                "replay": str(settings["expected_replay_lineage_sha256"]),
            },
            "pair_coverage": pair_coverage,
            "context_preflight": context_report,
            "decoder_pair_split": {
                key: value
                for key, value in decoder_split.items()
                if key not in {"calibration_pairs", "heldout_pairs"}
            },
            "teacher_cache_reuse": reuse,
            "frozen_selection_validation": frozen_selection_validation,
            "student_input_contract": input_contract,
            "field_algebra": field_algebra,
            "parameter_counts": parameter_counts,
            "resume_plan": {
                "teacher_cache": (
                    "atomic pair rows plus bare-state cache; skip only exact "
                    "validated keys"
                ),
                "decoder": (
                    "atomic u16/u32/u64/(u128) checkpoints with decoder, pair "
                    "latents, Adam state, RNG, and exact per-pair counters"
                ),
                "program": (
                    "atomic per-architecture/per-seed u16/u32/u64/(u128) "
                    "checkpoints with model, Adam state, RNG, frozen hashes, "
                    "and exact per-pair counters"
                ),
                "attempts": (
                    "append-only attempts.jsonl with parent attempt/resume "
                    "checkpoint identity"
                ),
                "heartbeat_seconds": int(settings["heartbeat_interval_seconds"]),
            },
            "runtime_projection": runtime,
            "artifact_paths": {
                "root": str(args.artifact_dir),
                "preflight": str(output_root),
                "unique_teacher_pairs": str(
                    output_root / "teacher_cache_unique_scoreable_pairs.jsonl"
                ),
                "decoder_split": str(output_root / "decoder_pair_split.json"),
            },
            "hard_scope": {
                "qwen_model_loaded": False,
                "qwen_forward_run": False,
                "gpu_used": False,
                "selector_modified": False,
                "historical_artifacts_rewritten": False,
                "stage_c2_started": False,
                "v4_tag_created": False,
            },
        }
        atomic_write_json(args.artifact_dir / "preflight_summary.json", summary)
        atomic_write_text(args.artifact_dir / "preflight_report.md", _report(summary))
        attempt.progress(
            status=summary["status"],
            latest_validated_checkpoint=str(args.artifact_dir / "preflight_summary.json"),
            pair_counts=counts,
            expected_h100_hours=runtime["scenarios"]["expected"]["h100_hours"],
        )
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
