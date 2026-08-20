from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
import torch
from transformers import AutoTokenizer

from rcmf.benchmarks.appworld.prompt import appworld_renderer_metadata
from rcmf.config import load_config
from rcmf.training.datasets import load_decision_examples
from rcmf.training.state_conditioned_program_7d import (
    build_frozen_cell_pairs,
    canonical_sha256,
    selector_candidate_projection,
)
from rcmf.training.state_conditioned_program_fast_7df import (
    build_bounded_a_pairs,
    fast_field_validation,
)
from rcmf.training.state_conditioned_transition_6b import (
    AttemptLedger,
    initialize_or_validate_run_manifest,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    write_jsonl,
)
from scripts.prepare_signature_balanced_field_7c import _query_signatures, _task_split
from scripts.prepare_state_conditioned_program_7d import (
    _attempt_ids,
    _context_builder,
    _context_preflight_manifests,
    _frozen_selection_comparison,
    _json,
    _load_inputs,
    _response_cache_reuse,
    _rows,
    _validate_immutable_inputs,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_state_conditioned_program_fast_7df.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", required=True)
    parser.add_argument("--tmux-session", default="exp025df")
    return parser.parse_args()


def _decoder_paths(settings: Mapping[str, Any]) -> dict[str, Path]:
    decoder = settings["decoder"]
    return {
        "direct_u112_checkpoint": Path(str(decoder["source_checkpoint"])),
        "direct_u112_rows": Path(str(decoder["source_rows"])),
        "direct_u112_pair_cache": Path(str(decoder["source_pair_cache"])),
        "clean_pair_cache": Path(str(decoder["clean_pair_cache"])),
    }


def _scalar_utilities(path: Path) -> dict[tuple[str, str], float]:
    output: dict[tuple[str, str], float] = {}
    for row in read_jsonl(path):
        if not bool(row.get("valid_for_loss")) or row.get("text_utility") is None:
            continue
        key = (str(row["state_example_id"]), str(row["transition_id"]))
        if key in output:
            raise ValueError(f"Duplicate clean scalar teacher key: {key}")
        output[key] = float(row["text_utility"])
    return output


def _decoder_repair_audit(
    *, settings: Mapping[str, Any], paths: Mapping[str, Path]
) -> dict[str, Any]:
    checkpoint = torch.load(
        paths["direct_u112_checkpoint"], map_location="cpu", weights_only=False
    )
    pair_ids = [str(value) for value in checkpoint["pair_ids"]]
    if len(pair_ids) != 192 or len(pair_ids) != len(set(pair_ids)):
        raise ValueError("Direct u112 checkpoint pair identity contract failed")
    old_rows = {str(row["pair_id"]): row for row in _rows(paths["direct_u112_rows"])}
    old_cache = {
        str(row["pair_id"]): row for row in _rows(paths["direct_u112_pair_cache"])
    }
    clean_cache = {
        str(row["pair_id"]): row for row in _rows(paths["clean_pair_cache"])
    }
    affected_task = str(settings["decoder"]["affected_memory_task"])
    affected = []
    for pair_id in pair_ids:
        source = old_cache.get(pair_id, old_rows.get(pair_id, {}))
        memory_task = str(
            source.get("memory_task_id", source.get("parent_task_id", ""))
        )
        if memory_task == affected_task:
            affected.append(pair_id)
    expected = int(settings["decoder"]["expected_repair_rows"])
    if len(affected) != expected:
        raise ValueError(
            f"Expected {expected} direct rows affected by {affected_task}, got {len(affected)}"
        )
    update_counts = [int(value) for value in checkpoint["update_counts"]]
    if update_counts != [112] * 192:
        raise ValueError("Direct u112 update accounting is not exactly 112 per pair")
    return {
        "source_pair_count": len(pair_ids),
        "source_pair_order_sha256": canonical_sha256(pair_ids),
        "affected_task_id": affected_task,
        "affected_pair_count": len(affected),
        "affected_pair_ids": affected,
        "affected_pairs_already_in_clean_pair_cache": sum(
            pair_id in clean_cache for pair_id in affected
        ),
        "affected_pairs_requiring_new_clean_pair_score": sum(
            pair_id not in clean_cache for pair_id in affected
        ),
        "unaffected_row_count_reused": len(pair_ids) - len(affected),
        "repair_strategy": "row_repair_then_uncentered_rank128_svd",
        "full_stage5fb_rerun": False,
    }


def _runtime_projection(
    *, settings: Mapping[str, Any], unique_pairs: int, new_teacher_rows: int
) -> dict[str, Any]:
    rates = settings["runtime"]["rates"]
    repair_pairs = int(settings["decoder"]["expected_repair_rows"])
    repair_updates = max(int(value) for value in settings["decoder"]["repair_updates"])
    pair_updates = max(int(value) for value in settings["pair_latents"]["updates"])
    stability_pairs = int(settings["pair_latents"]["stability_pair_count"])
    stability_updates = int(settings["pair_latents"]["stability_updates"])
    heldout_pairs = sum(
        int(settings["pair_manifest"][f"{cell.lower()}_pairs"])
        for cell in ("B", "C", "D", "E")
    )
    architecture_count = len(settings["program"]["architectures"])
    teacher_forced_forwards = heldout_pairs * architecture_count
    checkpoint_forwards = unique_pairs * len(settings["pair_latents"]["updates"])
    repair_forwards = repair_pairs * len(settings["decoder"]["repair_updates"])
    prefix_forwards = int(settings["prefix_cache"]["representative_pairs"]) * 4
    one_step_generations = (
        int(settings["one_step"]["audit_states"])
        * len(settings["one_step"]["conditions"])
    )
    base_backward = (
        repair_pairs * repair_updates
        + unique_pairs * pair_updates
        + stability_pairs * stability_updates
    )
    fallback_decoder_backward = (64 + 16) * 32
    optional_u64_backward = unique_pairs * 32
    forward_count = (
        new_teacher_rows
        + teacher_forced_forwards
        + checkpoint_forwards
        + repair_forwards
        + prefix_forwards
    )
    scenarios = {}
    for name in ("best", "expected", "conservative"):
        backward = base_backward
        if name == "conservative":
            backward += fallback_decoder_backward + optional_u64_backward
        seconds = (
            forward_count * float(rates[name]["forward"])
            + backward * float(rates[name]["backward"])
            + one_step_generations * float(rates[name]["generation"])
        )
        # Tensor-space program training is cheap but receives an explicit wall margin.
        margin_seconds = {"best": 600.0, "expected": 1200.0, "conservative": 2400.0}[name]
        scenarios[name] = {
            "qwen_forward_count": forward_count,
            "qwen_backward_updates": backward,
            "conditional_one_step_generations": one_step_generations,
            "tensor_program_training_margin_seconds": margin_seconds,
            "projected_seconds": seconds + margin_seconds,
            "h100_hours": (seconds + margin_seconds) / 3600.0,
        }
    expected_hours = float(scenarios["expected"]["h100_hours"])
    projected_bytes = (
        unique_pairs * int(settings["runtime"]["projected_bytes_per_teacher_row"])
        + len(settings["program"]["architectures"])
        * int(settings["runtime"]["projected_bytes_per_checkpoint"])
    )
    return {
        "scenarios": scenarios,
        "expected_h100_hours": expected_hours,
        "review_threshold_h100_hours": float(
            settings["runtime"]["review_threshold_h100_hours"]
        ),
        "automatic_launch_allowed": expected_hours
        <= float(settings["runtime"]["review_threshold_h100_hours"]),
        "projected_artifact_bytes": projected_bytes,
        "assumptions": {
            "repair_direct_rows": repair_pairs,
            "canonical_pair_updates": pair_updates,
            "optional_u64_only_in_conservative": True,
            "fallback_clean_decoder_only_in_conservative": True,
            "one_step_is_conditional_on_teacher_forced_gate": True,
        },
    }


def _coverage(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "pair_count": len(rows),
        "state_count": len({str(row["state_example_id"]) for row in rows}),
        "task_count": len({str(row["state_task_id"]) for row in rows}),
        "transition_count": len({str(row["transition_id"]) for row in rows}),
        "parent_count": len({str(row["transition_parent_id"]) for row in rows}),
        "role_counts": dict(sorted(Counter(str(row["pair_role"]) for row in rows).items())),
    }


def _report(summary: Mapping[str, Any]) -> str:
    runtime = summary["runtime_projection"]
    context = summary["context_preflight"]
    lines = [
        "# EXP-025D-Fast runtime preflight",
        "",
        f"- run UUID: `{summary['run_uuid']}`",
        f"- source commit: `{summary['source_commit']}`",
        f"- logical pairs: `{context['logical_pair_count']}`",
        f"- unique pairs: `{context['unique_pair_count']}`",
        f"- scoreable pairs: `{context['scoreable_pair_count']}`",
        f"- over-context pairs: `{context['over_context_pair_count']}`",
        "",
        "## H100 projection",
        "",
    ]
    for name, row in runtime["scenarios"].items():
        lines.append(f"- {name}: `{row['h100_hours']:.3f}` H100 hours")
    lines.extend(
        [
            f"- projected artifact bytes: `{runtime['projected_artifact_bytes']}`",
            f"- automatic launch allowed: `{runtime['automatic_launch_allowed']}`",
            "",
            "This preflight loaded only the tokenizer and CPU tensors. It did "
            "not load Qwen weights or use the GPU.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7df"]
    persistent_root = Path(str(settings["persistent_root"]))
    if os.name != "nt" and not os.path.ismount(persistent_root):
        raise RuntimeError("Persistent filesystem is not mounted")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")

    paths = _load_inputs(settings)
    paths.update(_decoder_paths(settings))
    missing = {name: str(path) for name, path in paths.items() if not path.exists()}
    if missing:
        raise FileNotFoundError(f"Missing immutable fast-pilot inputs: {missing}")
    source_hashes = {name: sha256_file(path) for name, path in paths.items()}
    config_sha = sha256_file(args.config)
    command_scope = [
        "immutable input validation",
        "bounded deterministic 232-pair manifest",
        "tokenizer-only context preflight",
        "direct-u112 row-repair provenance",
        "H100 runtime and storage projection",
        "no Qwen model load or forward",
    ]
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="cpu_pair_manifest_and_runtime_preflight",
        command=[str(value) for value in sys.argv],
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
        immutable = _validate_immutable_inputs(settings=settings, paths=paths)
        initialize_or_validate_run_manifest(
            args.artifact_dir / "run_manifest.json",
            run_uuid=str(settings["run_uuid"]),
            config_sha256=config_sha,
            data_manifest_hashes=source_hashes,
            source_commit=args.lambda_head,
            command_scope=command_scope,
        )
        decoder_repair = _decoder_repair_audit(settings=settings, paths=paths)
        examples = load_decision_examples(paths["decisions"])
        task_split = _task_split(Path(str(settings["reconciled_corpus_dir"])))
        query_rows, _ = _query_signatures(examples, task_split)
        transitions_list = [
            row
            for row in _rows(paths["transitions"])
            if task_split[str(row["parent_task_id"])] == "train"
        ]
        transitions = {str(row["transition_id"]): row for row in transitions_list}
        labels = _rows(paths["labels"])
        by_cell = {
            cell: [row for row in labels if str(row["cell"]) == cell]
            for cell in ("A", "B", "C", "D")
        }
        candidates = {
            cell: selector_candidate_projection(rows) for cell, rows in by_cell.items()
        }
        classes = {
            str(row["signature_class_id"]): row
            for row in _json(paths["signature_classes"])["classes"]
        }
        ensemble = torch.load(
            paths["selector_ensemble"], map_location="cpu", weights_only=False
        )
        scores = ensemble["scores"].to(torch.float32)
        ordered_state_ids = [str(value) for value in ensemble["ordered_state_ids"]]
        ordered_transition_ids = [
            str(value) for value in ensemble["ordered_transition_ids"]
        ]
        transition_token_counts = {
            transition_id: int(row["teacher_section_tokens"])
            for transition_id, row in transitions.items()
        }
        expected = settings["expected"]
        count_checks = {
            "train_states": sum(row["split"] == "train" for row in query_rows)
            == int(expected["train_decisions"]),
            "validation_states": sum(
                row["split"] == "validation" for row in query_rows
            )
            == int(expected["validation_decisions"]),
            "train_transitions": len(transitions) == int(expected["train_transitions"]),
            "signature_classes": len(classes) == int(expected["signature_classes"]),
            "ordered_states": set(ordered_state_ids)
            == {str(row["state_example_id"]) for row in query_rows},
            "ordered_transitions": set(ordered_transition_ids) == set(transitions),
        }
        if not all(count_checks.values()):
            raise ValueError(f"Clean fast-pilot count contract failed: {count_checks}")

        pair_cfg = settings["pair_manifest"]
        seed = int(pair_cfg["seed"])
        logical = {
            "A": build_bounded_a_pairs(
                labels_a=by_cell["A"],
                scalar_utilities=_scalar_utilities(paths["clean_scalar_transition_teacher"]),
                scores=scores,
                ordered_state_ids=ordered_state_ids,
                ordered_transition_ids=ordered_transition_ids,
                transition_token_counts=transition_token_counts,
                classes=classes,
                target_size=int(pair_cfg["a_pairs"]),
                seed=seed,
            ),
            "B": build_frozen_cell_pairs(
                candidate_rows=candidates["B"],
                scores=scores,
                ordered_state_ids=ordered_state_ids,
                ordered_transition_ids=ordered_transition_ids,
                transition_token_counts=transition_token_counts,
                classes=classes,
                state_count=int(pair_cfg["b_pairs"]),
                cell="B",
                seed=seed,
            ),
            "C": build_frozen_cell_pairs(
                candidate_rows=candidates["C"],
                scores=scores,
                ordered_state_ids=ordered_state_ids,
                ordered_transition_ids=ordered_transition_ids,
                transition_token_counts=transition_token_counts,
                classes=classes,
                state_count=int(pair_cfg["c_pairs"]),
                cell="C",
                seed=seed,
            ),
            "D": build_frozen_cell_pairs(
                candidate_rows=candidates["D"],
                scores=scores,
                ordered_state_ids=ordered_state_ids,
                ordered_transition_ids=ordered_transition_ids,
                transition_token_counts=transition_token_counts,
                classes=classes,
                state_count=int(pair_cfg["d_pairs"]),
                cell="D",
                seed=seed,
            ),
            "E": build_frozen_cell_pairs(
                candidate_rows=[*candidates["B"], *candidates["D"]],
                scores=scores,
                ordered_state_ids=ordered_state_ids,
                ordered_transition_ids=ordered_transition_ids,
                transition_token_counts=transition_token_counts,
                classes=classes,
                state_count=int(pair_cfg["e_pairs"]),
                cell="E",
                seed=seed,
            ),
        }
        logical_count = sum(int(value["pair_count"]) for value in logical.values())
        if logical_count != int(pair_cfg["expected_logical_pairs_before_deduplication"]):
            raise ValueError(f"Expected 232 logical pairs, got {logical_count}")

        attempt.progress(status="tokenizer_context_preflight")
        tokenizer = AutoTokenizer.from_pretrained(
            str(settings["teacher_cache"]["model_name"]),
            use_fast=True,
            local_files_only=True,
        )
        contexts, _ = _context_builder(
            tokenizer=tokenizer,
            examples=examples,
            prompt_profile=cfg.benchmark.prompt_profile,
        )
        preflighted, context_report = _context_preflight_manifests(
            manifests=logical,
            tokenizer=tokenizer,
            contexts=contexts,
            transitions=transitions,
            prompt_profile=cfg.benchmark.prompt_profile,
            context_limit=int(settings["teacher_cache"]["context_limit"]),
        )
        frozen_validation = {
            cell: _frozen_selection_comparison(
                preflighted[cell], _rows(paths[f"selector_{cell.lower()}_diagnostics"])
            )
            for cell in ("B", "D", "E")
        }
        if not all(value["passed"] for value in frozen_validation.values()):
            raise ValueError("Fast-pilot selections differ from frozen EXP-025C rows")

        all_unique = {
            str(row["pair_id"]): row
            for rows in preflighted.values()
            for row in rows
            if not bool(row["over_context"])
        }
        renderer_version = str(
            appworld_renderer_metadata(
                cfg.benchmark.prompt_profile, add_generation_prompt=True
            )["renderer_version"]
        )
        _, reuse = _response_cache_reuse(
            rows=list(all_unique.values()),
            paths=paths,
            renderer_version=renderer_version,
            clean_lineage_sha256=str(settings["expected_structural_lineage_sha256"]),
        )
        runtime = _runtime_projection(
            settings=settings,
            unique_pairs=len(all_unique),
            new_teacher_rows=int(reuse["new_top64_rows"]),
        )
        field_validation = fast_field_validation(seed + 1)
        if not field_validation["passed"]:
            raise RuntimeError("Fast incremental field validation failed")

        output_root = args.artifact_dir / "preflight"
        output_root.mkdir(parents=True, exist_ok=True)
        for cell, rows in preflighted.items():
            write_jsonl(output_root / f"pairs_{cell}.jsonl", rows)
        write_jsonl(
            output_root / "teacher_cache_unique_scoreable_pairs.jsonl",
            sorted(all_unique.values(), key=lambda row: str(row["pair_id"])),
        )
        atomic_write_json(output_root / "context_preflight.json", context_report)
        atomic_write_json(output_root / "decoder_repair_audit.json", decoder_repair)
        atomic_write_json(output_root / "field_validation.json", field_validation)
        summary = {
            "format": "state_conditioned_program_fast_preflight_7df_v1",
            "status": "completed_ready_for_gpu"
            if runtime["automatic_launch_allowed"]
            else "completed_runtime_review_required",
            "run_uuid": str(settings["run_uuid"]),
            "source_commit": args.lambda_head,
            "immutable_validation": immutable,
            "count_checks": count_checks,
            "pair_coverage": {
                cell: _coverage(rows) for cell, rows in preflighted.items()
            },
            "context_preflight": context_report,
            "teacher_cache_reuse": reuse,
            "decoder_repair": decoder_repair,
            "frozen_selection_validation": frozen_validation,
            "field_validation": field_validation,
            "runtime_projection": runtime,
            "resume_plan": {
                "teacher_cache": "atomic pair rows; exact key/hash validation before skip",
                "decoder_repair": "atomic u8/u16/u32 rows and repaired rank128 decoder checkpoint",
                "pair_latents": "atomic u8/u16/u32 checkpoints with Adam/RNG/update counts",
                "program": "atomic architecture/seed tensor-space checkpoints",
                "teacher_forced": "atomic pair/model rows",
                "one_step": "EXP-025B live bridge atomic condition rows",
                "heartbeat_seconds": int(settings["heartbeat_interval_seconds"]),
            },
            "hard_scope": {
                "qwen_model_loaded": False,
                "qwen_forward_run": False,
                "gpu_used": False,
                "old_exp025d_artifact_rewritten": False,
                "selector_modified": False,
                "v4_tag_created": False,
            },
        }
        atomic_write_json(args.artifact_dir / "preflight_summary.json", summary)
        atomic_write_text(args.artifact_dir / "preflight_report.md", _report(summary))
        attempt.progress(
            status=summary["status"],
            latest_validated_checkpoint=str(args.artifact_dir / "preflight_summary.json"),
            logical_pairs=context_report["logical_pair_count"],
            unique_pairs=context_report["unique_pair_count"],
            expected_h100_hours=runtime["expected_h100_hours"],
        )
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
