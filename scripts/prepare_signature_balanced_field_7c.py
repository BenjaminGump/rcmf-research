from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
import torch
from transformers import AutoTokenizer

from rcmf.config import load_config
from rcmf.training.datasets import load_decision_examples
from rcmf.training.multiview_representations_6c import (
    LAYER_CANDIDATES,
    query_state_text_and_char_spans,
    transition_text_and_char_spans,
)
from rcmf.training.oracle_convergence_5fb import tensor_state_sha256
from rcmf.training.procedural_supervision_6f import (
    canonical_procedure_signature,
    procedural_compatibility,
    state_stage_signature,
)
from rcmf.training.signature_balanced_field_7c import (
    LABEL_FORMAT,
    canonical_hash,
    state_class_balanced_weights,
    validate_class_balance,
)
from rcmf.training.state_conditioned_transition_6b import (
    AttemptLedger,
    initialize_or_validate_run_manifest,
)
from rcmf.training.transition_memory_6a import (
    example_task_id,
    is_legal_transition_pair,
    state_example_id,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _atomic_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
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


def _task_split(corpus: Path) -> dict[str, str]:
    payload = _json(corpus / "train_validation_task_manifest.json")
    output = {str(value): "train" for value in payload["train_task_ids"]}
    output.update(
        {str(value): "validation" for value in payload["validation_task_ids"]}
    )
    return output


def _query_signatures(
    examples: Sequence[Any], task_split: Mapping[str, str]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows = []
    by_id = {}
    for index, example in enumerate(examples):
        state_id = state_example_id(index, example)
        task_id = example_task_id(example)
        action = canonical_procedure_signature(
            str(example.target_text), context_text=str(example.state_text)
        )
        stage = state_stage_signature(str(example.state_text))
        row = {
            "format": "clean_all_decision_procedural_signature_7c_v1",
            "state_example_id": state_id,
            "example_index": index,
            "task_id": task_id,
            "split": task_split[task_id],
            "step_id": int(example.step_id),
            "state_text_sha256": sha256_text(str(example.state_text)),
            "target_text_sha256": sha256_text(str(example.target_text)),
            "target_signature": action,
            "state_stage_signature": stage,
        }
        rows.append(row)
        by_id[state_id] = row
    if len(by_id) != len(rows):
        raise ValueError("Clean decision states have duplicate identities")
    return rows, by_id


def _candidate_spaces(
    transitions: Sequence[Mapping[str, Any]],
    parent_split: Mapping[str, Any],
    class_by_transition: Mapping[str, str],
    transition_signatures: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    definitions = {
        "strict_train_parent": "train",
        "heldout_parent": "heldout",
        "deployment_full_training": None,
    }
    output = {}
    for name, split in definitions.items():
        selected = [
            row
            for row in transitions
            if split is None
            or parent_split["split_by_parent"][str(row["parent_memory_id"])] == split
        ]
        class_counts = Counter(
            class_by_transition[str(row["transition_id"])] for row in selected
        )
        output[name] = {
            "transition_ids": sorted(str(row["transition_id"]) for row in selected),
            "transition_count": len(selected),
            "signature_class_count": len(class_counts),
            "class_sizes": dict(sorted(class_counts.items())),
            "parent_count": len({str(row["parent_memory_id"]) for row in selected}),
            "source_task_count": len({str(row["parent_task_id"]) for row in selected}),
            "api_documentation_transition_count": sum(
                bool(
                    transition_signatures[str(row["transition_id"])][
                        "action_signature"
                    ]["api_documentation_action"]
                )
                for row in selected
            ),
        }
    payload = {
        "format": "signature_balanced_candidate_spaces_7c_v1",
        "spaces": output,
    }
    payload["manifest_sha256"] = canonical_hash(payload)
    return payload


def _build_labels(
    *,
    examples: Sequence[Any],
    query_signatures: Mapping[str, Mapping[str, Any]],
    transitions: Sequence[Mapping[str, Any]],
    transition_signatures: Mapping[str, Mapping[str, Any]],
    class_by_transition: Mapping[str, str],
    parent_split: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    legal = []
    illegal = []
    cell_name = {
        ("train", "train"): "A",
        ("validation", "train"): "B",
        ("train", "heldout"): "C",
        ("validation", "heldout"): "D",
    }
    for index, example in enumerate(examples):
        state_id = state_example_id(index, example)
        query = query_signatures[state_id]
        state_split = str(query["split"])
        for transition in transitions:
            transition_id = str(transition["transition_id"])
            pair_id = f"{state_id}::transition::{transition_id}"
            if not is_legal_transition_pair(example, transition):
                illegal.append(
                    {
                        "pair_id": pair_id,
                        "state_example_id": state_id,
                        "state_task_id": str(query["task_id"]),
                        "transition_id": transition_id,
                        "transition_parent_id": str(transition["parent_memory_id"]),
                        "transition_parent_task_id": str(transition["parent_task_id"]),
                    }
                )
                continue
            transition_signature = transition_signatures[transition_id]
            compatibility = procedural_compatibility(
                query["target_signature"],
                query["state_stage_signature"],
                transition_signature["action_signature"],
                transition_signature["pre_action_stage_signature"],
                transition_signature["post_action_observation_signature"],
            )
            transition_split = str(
                parent_split["split_by_parent"][str(transition["parent_memory_id"])]
            )
            legal.append(
                {
                    "format": LABEL_FORMAT,
                    "pair_id": pair_id,
                    "state_example_id": state_id,
                    "state_index": index,
                    "state_task_id": str(query["task_id"]),
                    "state_split": state_split,
                    "state_step_id": int(query["step_id"]),
                    "query_primary_app": str(
                        query["target_signature"]["primary_app"]
                    ),
                    "query_primary_api": str(
                        query["target_signature"]["primary_api"]
                    ),
                    "query_coarse_action_type": str(
                        query["target_signature"]["coarse_action_type"]
                    ),
                    "query_api_documentation_action": bool(
                        query["target_signature"]["api_documentation_action"]
                    ),
                    "transition_id": transition_id,
                    "transition_index": int(transition["step_index"]),
                    "transition_parent_id": str(transition["parent_memory_id"]),
                    "transition_parent_task_id": str(transition["parent_task_id"]),
                    "transition_split": transition_split,
                    "transition_primary_app": str(
                        transition_signature["action_signature"]["primary_app"]
                    ),
                    "transition_primary_api": str(
                        transition_signature["action_signature"]["primary_api"]
                    ),
                    "transition_coarse_action_type": str(
                        transition_signature["action_signature"][
                            "coarse_action_type"
                        ]
                    ),
                    "transition_api_documentation_action": bool(
                        transition_signature["action_signature"][
                            "api_documentation_action"
                        ]
                    ),
                    "signature_class_id": class_by_transition[transition_id],
                    "cell": cell_name[(state_split, transition_split)],
                    "procedural_tier": int(compatibility["tier"]),
                    **compatibility,
                }
            )
    if len({str(row["pair_id"]) for row in [*legal, *illegal]}) != len(
        legal
    ) + len(illegal):
        raise ValueError("Procedural pair keys are duplicated")
    return legal, illegal


def _cell_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def summarize(selected: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in selected:
            grouped[str(row["state_example_id"])].append(row)

        def has_same_intent_hard_pair(
            values: Sequence[Mapping[str, Any]],
        ) -> bool:
            tiers_by_intent: dict[str, set[int]] = defaultdict(set)
            for value in values:
                tiers_by_intent[str(value["transition_coarse_action_type"])].add(
                    int(value["procedural_tier"])
                )
            return any(len(tiers) > 1 for tiers in tiers_by_intent.values())

        return {
            "pair_count": len(selected),
            "state_count": len(grouped),
            "task_count": len({str(row["state_task_id"]) for row in selected}),
            "transition_count": len({str(row["transition_id"]) for row in selected}),
            "parent_count": len(
                {str(row["transition_parent_id"]) for row in selected}
            ),
            "signature_class_count": len(
                {str(row["signature_class_id"]) for row in selected}
            ),
            "tier_counts": dict(
                sorted(Counter(int(row["procedural_tier"]) for row in selected).items())
            ),
            "tier34_state_coverage": (
                sum(
                    any(int(row["procedural_tier"]) >= 3 for row in values)
                    for values in grouped.values()
                )
                / len(grouped)
                if grouped
                else 0.0
            ),
            "exact_api_state_coverage": (
                sum(
                    any(bool(row["exact_api_sequence"]) for row in values)
                    for values in grouped.values()
                )
                / len(grouped)
                if grouped
                else 0.0
            ),
            "same_intent_hard_pair_states": sum(
                has_same_intent_hard_pair(values)
                for values in grouped.values()
            ),
            "query_action_type_state_counts": dict(
                sorted(
                    Counter(
                        str(values[0]["query_coarse_action_type"])
                        for values in grouped.values()
                    ).items()
                )
            ),
            "query_api_documentation_state_count": sum(
                bool(values[0]["query_api_documentation_action"])
                for values in grouped.values()
            ),
            "transition_api_documentation_pair_count": sum(
                bool(row["transition_api_documentation_action"])
                for row in selected
            ),
        }
    output = {
        cell: summarize([row for row in rows if str(row["cell"]) == cell])
        for cell in "ABCD"
    }
    heldout = [row for row in rows if str(row["cell"]) in {"B", "D"}]
    output["E"] = {
        **summarize(heldout),
        "definition": "B_union_D",
    }
    return output


def _multiview_preflight(
    *,
    tokenizer: Any,
    examples: Sequence[Any],
    transitions: Sequence[Mapping[str, Any]],
    old_state_path: Path,
    old_transition_path: Path,
    prompt_profile: str,
    renderer_version: str,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    old_state = torch.load(old_state_path, map_location="cpu", weights_only=False)
    old_transition = torch.load(
        old_transition_path, map_location="cpu", weights_only=False
    )
    for layer in LAYER_CANDIDATES:
        if tensor_state_sha256(
            {"representations": old_state["representations"][layer]}
        ) != old_state["tensor_sha256"][layer]:
            raise ValueError(f"Old state multiview tensor hash differs: {layer}")
        if tensor_state_sha256(
            {"representations": old_transition["representations"][layer]}
        ) != old_transition["tensor_sha256"][layer]:
            raise ValueError(f"Old transition multiview tensor hash differs: {layer}")
    if str(old_state["renderer_version"]) != renderer_version:
        raise ValueError("Old state multiview renderer differs")
    old_state_rows = {
        str(row["state_example_id"]): row for row in old_state["rows"]
    }
    reusable_states = []
    recomputed_states = []
    for index, example in enumerate(examples):
        state_id = state_example_id(index, example)
        rendered, _, _ = query_state_text_and_char_spans(
            tokenizer, example, prompt_profile
        )
        row = old_state_rows.get(state_id)
        if (
            row is not None
            and str(row["prompt_sha256"]) == sha256_text(rendered)
            and str(row["target_sha256"]) == sha256_text(str(example.target_text))
            and str(row.get("renderer_version")) == renderer_version
            and str(row.get("model_name"))
            == str(settings["multiview_cache"]["model_name"])
        ):
            reusable_states.append(state_id)
        else:
            recomputed_states.append(state_id)
    old_transition_rows = {
        str(row["transition_id"]): row for row in old_transition["rows"]
    }
    reusable_transitions = []
    recomputed_transitions = []
    for row in transitions:
        transition_id = str(row["transition_id"])
        old = old_transition_rows.get(transition_id)
        rendered, _, _ = transition_text_and_char_spans(row)
        if old is not None and (
            str(old["transition_content_sha256"])
            == str(row["transition_content_sha256"])
            and str(old.get("teacher_section_sha256")) == sha256_text(rendered)
            and str(old.get("renderer_version")) == renderer_version
            and str(old.get("model_name"))
            == str(settings["multiview_cache"]["model_name"])
        ):
            reusable_transitions.append(transition_id)
        else:
            recomputed_transitions.append(transition_id)
    expected = settings["expected"]
    checks = {
        "reused_states": len(reusable_states)
        == int(settings["multiview_cache"]["expected_reused_states"]),
        "recomputed_states": len(recomputed_states)
        == int(settings["multiview_cache"]["expected_recomputed_states"]),
        "reused_transitions": len(reusable_transitions)
        == int(settings["multiview_cache"]["expected_reused_transitions"]),
        "recomputed_transitions": len(recomputed_transitions)
        == int(settings["multiview_cache"]["expected_recomputed_transitions"]),
        "state_total": len(examples)
        == int(expected["train_decisions"]) + int(expected["validation_decisions"]),
        "transition_total": len(transitions) == int(expected["train_transitions"]),
    }
    if not all(checks.values()):
        raise ValueError(
            f"Multiview provenance preflight differs: "
            f"{[name for name, passed in checks.items() if not passed]}"
        )
    new_rows = len(recomputed_states) + len(recomputed_transitions)
    runtime = {}
    for name, seconds in settings["multiview_cache"][
        "runtime_seconds_per_new_row"
    ].items():
        runtime[name] = {
            "new_qwen_forward_count": new_rows,
            "h100_hours": new_rows * float(seconds) / 3600.0,
            "wall_hours": new_rows * float(seconds) / 3600.0,
        }
    threshold = float(settings["multiview_cache"]["review_threshold_h100_hours"])
    return {
        "format": "clean_multiview_provenance_preflight_7c_v1",
        "checks": checks,
        "state": {
            "total": len(examples),
            "reused": len(reusable_states),
            "recomputed": len(recomputed_states),
            "recomputed_ids": sorted(recomputed_states),
        },
        "transition": {
            "total": len(transitions),
            "reused": len(reusable_transitions),
            "recomputed": len(recomputed_transitions),
            "recomputed_ids": sorted(recomputed_transitions),
        },
        "new_qwen_forward_count": new_rows,
        "runtime_projection": runtime,
        "projected_artifact_bytes": (
            (len(examples) + len(transitions))
            * int(settings["multiview_cache"]["artifact_bytes_per_row"])
        ),
        "review_threshold_h100_hours": threshold,
        "requires_explicit_runtime_approval": runtime["expected"]["h100_hours"]
        > threshold,
        "resume_plan": (
            "atomic per-row tensors; aggregate written only after every row hash "
            "validates; attempt ledger records the latest row"
        ),
    }


def _report(summary: Mapping[str, Any]) -> str:
    cache = summary["multiview_preflight"]
    cells = summary["cells"]
    lines = [
        "# EXP-025C Clean Procedural Label and Cache Preflight",
        "",
        "## VERIFIED",
        "",
        f"- clean decision states: `{summary['counts']['decision_states']}`",
        f"- clean train transitions: `{summary['counts']['train_transitions']}`",
        f"- legal/illegal procedural pairs: `{summary['counts']['legal_pairs']}` / "
        f"`{summary['counts']['illegal_pairs']}`",
        f"- signature classes: `{summary['counts']['signature_classes']}`",
        f"- class-balance validation: `{summary['class_balance']['passed']}`",
        "",
        "| Cell | Pairs | States | Classes | Tier-3/4 coverage | Exact API coverage |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for cell in "ABCDE":
        row = cells[cell]
        lines.append(
            f"| {cell} | {row['pair_count']} | {row['state_count']} | "
            f"{row['signature_class_count']} | {row['tier34_state_coverage']:.4f} | "
            f"{row['exact_api_state_coverage']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Frozen-Qwen Multiview Cache",
            "",
            f"- state rows reused/recomputed: `{cache['state']['reused']}` / "
            f"`{cache['state']['recomputed']}`",
            f"- transition rows reused/recomputed: `{cache['transition']['reused']}` / "
            f"`{cache['transition']['recomputed']}`",
            f"- new frozen-Qwen forwards: `{cache['new_qwen_forward_count']}`",
            f"- expected H100 hours: "
            f"`{cache['runtime_projection']['expected']['h100_hours']:.4f}`",
            f"- projected artifact bytes: `{cache['projected_artifact_bytes']}`",
            f"- exceeds 12-H100-hour review threshold: "
            f"`{cache['requires_explicit_runtime_approval']}`",
            "",
            "No raw-NLL utility, prior model prediction, Qwen generation, or "
            "AppWorld outcome was used to construct these labels.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_signature_balanced_field_7c.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id")
    parser.add_argument("--resume-checkpoint")
    parser.add_argument("--tmux-session", default="exp025c")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7c"]
    if os.name != "nt" and not os.path.ismount(Path(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    parent = Path(settings["parent_exp025b"])
    corpus = Path(settings["reconciled_corpus_dir"])
    replay_manifest = _json(parent / "replay_validated_corpus_manifest.json")
    if replay_manifest["lineage_sha256"] != settings[
        "expected_replay_lineage_sha256"
    ]:
        raise ValueError("Replay-validated lineage differs")
    if replay_manifest["structural_corpus_lineage_sha256"] != settings[
        "expected_structural_lineage_sha256"
    ]:
        raise ValueError("Structural corpus lineage differs")
    paths = {
        "replay_manifest": parent / "replay_validated_corpus_manifest.json",
        "corpus_summary": corpus / "summary.json",
        "structural_validation": corpus / "structural_validation.json",
        "decisions": corpus / "decision_examples.jsonl",
        "transitions": corpus / "transition_manifest.jsonl",
        "parent_split": parent
        / "clean_procedural_audit/clean_parent_split_manifest.json",
        "transition_signatures": parent
        / "clean_procedural_audit/clean_transition_signature_manifest.jsonl",
        "signature_classes": parent
        / "clean_procedural_audit/clean_signature_equivalence_manifest.json",
        "old_state_multiview": Path(
            settings["multiview_cache"]["old_state_cache"]
        ),
        "old_transition_multiview": Path(
            settings["multiview_cache"]["old_transition_cache"]
        ),
    }
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Required immutable input missing: {name}={path}")
    config_hash = sha256_file(args.config)
    data_hashes = {name: sha256_file(path) for name, path in paths.items()}
    initialize_or_validate_run_manifest(
        args.artifact_dir / "run_manifest.json",
        run_uuid=str(settings["run_uuid"]),
        config_sha256=config_hash,
        data_manifest_hashes=data_hashes,
        source_commit=args.lambda_head,
        command_scope=[
            "clean procedural labels",
            "signature-class balancing",
            "multiview provenance preflight",
            "no Qwen forward or model training",
        ],
    )
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="cpu_data_and_multiview_preflight",
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
        examples = load_decision_examples(paths["decisions"])
        task_split = _task_split(corpus)
        query_rows, query_by_id = _query_signatures(examples, task_split)
        transitions = [
            row
            for row in _rows(paths["transitions"])
            if task_split[str(row["parent_task_id"])] == "train"
        ]
        parent_split = _json(paths["parent_split"])
        transition_signature_rows = _rows(paths["transition_signatures"])
        transition_signatures = {
            str(row["transition_id"]): row for row in transition_signature_rows
        }
        class_manifest = _json(paths["signature_classes"])
        class_by_transition = {
            str(transition_id): str(row["signature_class_id"])
            for row in class_manifest["classes"]
            for transition_id in row["member_transition_ids"]
        }
        expected = settings["expected"]
        checks = {
            "train_states": sum(row["split"] == "train" for row in query_rows)
            == int(expected["train_decisions"]),
            "validation_states": sum(
                row["split"] == "validation" for row in query_rows
            )
            == int(expected["validation_decisions"]),
            "transitions": len(transitions) == int(expected["train_transitions"]),
            "transition_signatures": set(transition_signatures)
            == {str(row["transition_id"]) for row in transitions},
            "signature_classes": int(class_manifest["signature_class_count"])
            == int(expected["signature_classes"]),
            "parent_train": int(parent_split["train_parent_count"])
            == int(expected["parent_split_train"]),
            "parent_heldout": int(parent_split["heldout_parent_count"])
            == int(expected["parent_split_heldout"]),
        }
        if not all(checks.values()):
            raise ValueError(
                f"Clean input contract differs: "
                f"{[name for name, passed in checks.items() if not passed]}"
            )
        candidate_spaces = _candidate_spaces(
            transitions,
            parent_split,
            class_by_transition,
            transition_signatures,
        )
        attempt.progress(status="building_clean_procedural_labels")
        labels, illegal = _build_labels(
            examples=examples,
            query_signatures=query_by_id,
            transitions=transitions,
            transition_signatures=transition_signatures,
            class_by_transition=class_by_transition,
            parent_split=parent_split,
        )
        training_rows = [row for row in labels if row["cell"] == "A"]
        weights = state_class_balanced_weights(training_rows)
        balance = validate_class_balance(training_rows, weights)
        if not balance["passed"]:
            raise RuntimeError("Signature-class balancing validation failed")
        tokenizer = AutoTokenizer.from_pretrained(
            str(settings["multiview_cache"]["model_name"]),
            use_fast=True,
            local_files_only=True,
        )
        cache_preflight = _multiview_preflight(
            tokenizer=tokenizer,
            examples=examples,
            transitions=transitions,
            old_state_path=paths["old_state_multiview"],
            old_transition_path=paths["old_transition_multiview"],
            prompt_profile=cfg.benchmark.prompt_profile,
            renderer_version=str(
                settings["multiview_cache"]["renderer_version"]
            ),
            settings=settings,
        )
        query_path = args.artifact_dir / "clean_query_signature_manifest.jsonl"
        labels_path = args.artifact_dir / "clean_full_procedural_labels.jsonl"
        illegal_path = args.artifact_dir / "clean_full_illegal_pairs.jsonl"
        candidate_path = args.artifact_dir / "candidate_space_manifest.json"
        _atomic_jsonl(query_path, query_rows)
        _atomic_jsonl(labels_path, labels)
        _atomic_jsonl(illegal_path, illegal)
        atomic_write_json(candidate_path, candidate_spaces)
        atomic_write_json(
            args.artifact_dir / "multiview_cache_preflight.json", cache_preflight
        )
        summary = {
            "format": "signature_balanced_field_preparation_7c_v1",
            "status": "completed",
            "run_uuid": str(settings["run_uuid"]),
            "input_checks": checks,
            "counts": {
                "decision_states": len(query_rows),
                "train_states": sum(row["split"] == "train" for row in query_rows),
                "validation_states": sum(
                    row["split"] == "validation" for row in query_rows
                ),
                "train_transitions": len(transitions),
                "signature_classes": int(class_manifest["signature_class_count"]),
                "cartesian_pairs": len(query_rows) * len(transitions),
                "legal_pairs": len(labels),
                "illegal_pairs": len(illegal),
            },
            "cells": _cell_summary(labels),
            "class_balance": balance,
            "candidate_spaces": candidate_spaces,
            "multiview_preflight": cache_preflight,
            "hashes": {
                "queries": sha256_file(query_path),
                "labels": sha256_file(labels_path),
                "illegal": sha256_file(illegal_path),
                "candidate_spaces": sha256_file(candidate_path),
            },
            "hard_scope": {
                "raw_nll_used": False,
                "behavioral_outcomes_used": False,
                "qwen_forward_run": False,
                "model_trained": False,
                "prior_artifacts_rewritten": False,
            },
        }
        summary_path = args.artifact_dir / "data_preparation_summary.json"
        atomic_write_json(summary_path, summary)
        atomic_write_text(args.artifact_dir / "clean_full_procedural_label_report.md", _report(summary))
        attempt.progress(status="completed", latest_validated_checkpoint=str(summary_path))
        print(json.dumps(summary, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
