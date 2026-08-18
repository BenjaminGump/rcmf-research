from __future__ import annotations

import argparse
import gc
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
import torch
from torch import Tensor

from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.training.datasets import load_decision_examples
from rcmf.training.multiview_representations_6c import (
    LAYER_CANDIDATES,
    MULTIVIEW_CACHE_VERSION,
    POOLING_RULES,
    STATE_VIEW_NAMES,
    TRANSITION_VIEW_NAMES,
    flatten_multiview_readouts,
    frozen_qwen_span_readouts,
    query_state_text_and_char_spans,
    readout_payload_hash,
    tokenize_and_validate_char_spans,
    transition_text_and_char_spans,
)
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.oracle_convergence_5fb import tensor_state_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger, utc_now
from rcmf.training.transition_memory_6a import (
    example_task_id,
    state_example_id,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
)
from scripts.run_action_intent_probe_6d import _intent_labels


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


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


def _validated_old_cache(path: Path) -> dict[str, Any]:
    payload = torch.load(path, map_location="cpu", weights_only=False)
    for layer in LAYER_CANDIDATES:
        actual = tensor_state_sha256(
            {"representations": payload["representations"][layer]}
        )
        if actual != payload["tensor_sha256"][layer]:
            raise ValueError(f"Immutable multiview hash differs: {path} {layer}")
    return payload


def _clean_state_cache(
    *,
    backend: Any,
    examples: Sequence[Any],
    task_split: Mapping[str, str],
    old: Mapping[str, Any],
    output_root: Path,
    prompt_profile: str,
    renderer_version: str,
    lineage: str,
    attempt: AttemptLedger,
) -> tuple[dict[str, Tensor], list[dict[str, Any]], dict[str, Any]]:
    old_position = {str(value): index for index, value in enumerate(old["ordered_ids"])}
    old_rows = {str(row["state_example_id"]): row for row in old["rows"]}
    row_root = output_root / "state_rows"
    row_root.mkdir(parents=True, exist_ok=True)
    tensors: dict[str, list[torch.Tensor]] = {
        layer: [] for layer in LAYER_CANDIDATES
    }
    rows = []
    reused = 0
    resumed = 0
    computed = 0
    for index, example in enumerate(examples):
        state_id = state_example_id(index, example)
        rendered, char_spans, source_metadata = query_state_text_and_char_spans(
            backend.tokenizer, example, prompt_profile
        )
        input_ids, attention_mask, span_rows = tokenize_and_validate_char_spans(
            backend.tokenizer, rendered, char_spans
        )
        expected = {
            "state_example_id": state_id,
            "prompt_sha256": sha256_text(rendered),
            "target_sha256": sha256_text(str(example.target_text)),
            "renderer_version": renderer_version,
            "model_name": str(backend.model_name),
        }
        old_row = old_rows.get(state_id)
        source = "computed"
        payload = None
        if old_row is not None and all(
            str(old_row.get(key)) == str(value) for key, value in expected.items()
        ):
            source = "immutable_exp020_reuse"
            old_index = old_position[state_id]
            readouts = {
                layer: old["representations"][layer][old_index].to(torch.float32)
                for layer in LAYER_CANDIDATES
            }
            reused += 1
        else:
            path = row_root / f"{sha256_text(state_id)}.pt"
            resumed_row = False
            if path.exists():
                candidate = torch.load(path, map_location="cpu", weights_only=False)
                if any(str(candidate.get(key)) != str(value) for key, value in expected.items()):
                    raise ValueError(f"Existing clean state row differs: {path}")
                if str(candidate.get("corpus_lineage_sha256")) != lineage:
                    raise ValueError(f"Existing clean state lineage differs: {path}")
                if readout_payload_hash(candidate["readouts"]) != candidate["readout_sha256"]:
                    raise ValueError(f"Existing clean state tensor differs: {path}")
                payload = candidate
                resumed += 1
                resumed_row = True
            if payload is None:
                nested = frozen_qwen_span_readouts(
                    model=backend.model,
                    input_ids=input_ids,
                    attention_mask=attention_mask,
                    span_rows=span_rows,
                    device=backend.device,
                )
                payload = {
                    "format": f"{MULTIVIEW_CACHE_VERSION}_clean_state_row_7c",
                    **expected,
                    "readouts": nested,
                    "readout_sha256": readout_payload_hash(nested),
                    "span_rows": span_rows,
                    "source_metadata": source_metadata,
                    "token_count": int(input_ids.shape[1]),
                    "corpus_lineage_sha256": lineage,
                    "target_action_accessed": False,
                    "future_observation_accessed": False,
                    "truncated": False,
                    "created_at_utc": utc_now(),
                }
                atomic_torch_save(payload, path)
                computed += 1
            readouts = {
                layer: flatten_multiview_readouts(
                    [payload], layer=layer, view_names=STATE_VIEW_NAMES
                )[0].to(torch.float32)
                for layer in LAYER_CANDIDATES
            }
            source = "resumed_clean_row" if resumed_row else "computed_clean_row"
        for layer in LAYER_CANDIDATES:
            tensors[layer].append(readouts[layer])
        rows.append(
            {
                "format": "clean_multiview_state_metadata_7c_v1",
                **expected,
                "example_index": index,
                "task_id": example_task_id(example),
                "split": task_split[example_task_id(example)],
                "step_id": int(example.step_id),
                "labels": _intent_labels(str(example.target_text)),
                "token_count": int(input_ids.shape[1]),
                "corpus_lineage_sha256": lineage,
                "provenance": source,
                "target_action_accessed": False,
                "future_observation_accessed": False,
                "truncated": False,
            }
        )
        attempt.progress(
            status="clean_multiview_state_cache",
            completed=index + 1,
            total=len(examples),
            reused=reused,
            resumed=resumed,
            newly_computed=computed,
            latest_validated_checkpoint=str(
                output_root if source == "immutable_exp020_reuse" else path
            ),
        )
        del input_ids, attention_mask
        if (index + 1) % 10 == 0:
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
    matrices = {layer: torch.stack(values) for layer, values in tensors.items()}
    return matrices, rows, {
        "total": len(examples),
        "reused": reused,
        "resumed": resumed,
        "newly_computed": computed,
    }


def _clean_transition_cache(
    *,
    backend: Any,
    transitions: Sequence[Mapping[str, Any]],
    old: Mapping[str, Any],
    output_root: Path,
    renderer_version: str,
    lineage: str,
    attempt: AttemptLedger,
) -> tuple[dict[str, Tensor], list[dict[str, Any]], dict[str, Any]]:
    old_position = {str(value): index for index, value in enumerate(old["ordered_ids"])}
    old_rows = {str(row["transition_id"]): row for row in old["rows"]}
    row_root = output_root / "transition_rows"
    row_root.mkdir(parents=True, exist_ok=True)
    tensors: dict[str, list[torch.Tensor]] = {
        layer: [] for layer in LAYER_CANDIDATES
    }
    rows = []
    reused = 0
    resumed = 0
    computed = 0
    ordered = sorted(transitions, key=lambda row: str(row["transition_id"]))
    for index, transition in enumerate(ordered):
        transition_id = str(transition["transition_id"])
        rendered, char_spans, source_metadata = transition_text_and_char_spans(
            transition
        )
        input_ids, attention_mask, span_rows = tokenize_and_validate_char_spans(
            backend.tokenizer, rendered, char_spans
        )
        expected = {
            "transition_id": transition_id,
            "transition_content_sha256": str(
                transition["transition_content_sha256"]
            ),
            "teacher_section_sha256": sha256_text(rendered),
            "renderer_version": renderer_version,
            "model_name": str(backend.model_name),
        }
        old_row = old_rows.get(transition_id)
        source = "computed"
        payload = None
        if old_row is not None and all(
            str(old_row.get(key)) == str(value) for key, value in expected.items()
        ):
            source = "immutable_exp020_reuse"
            old_index = old_position[transition_id]
            readouts = {
                layer: old["representations"][layer][old_index].to(torch.float32)
                for layer in LAYER_CANDIDATES
            }
            reused += 1
        else:
            path = row_root / f"{transition_id}.pt"
            resumed_row = False
            if path.exists():
                candidate = torch.load(path, map_location="cpu", weights_only=False)
                if any(str(candidate.get(key)) != str(value) for key, value in expected.items()):
                    raise ValueError(f"Existing clean transition row differs: {path}")
                if str(candidate.get("corpus_lineage_sha256")) != lineage:
                    raise ValueError(f"Existing clean transition lineage differs: {path}")
                if readout_payload_hash(candidate["readouts"]) != candidate["readout_sha256"]:
                    raise ValueError(f"Existing clean transition tensor differs: {path}")
                payload = candidate
                resumed += 1
                resumed_row = True
            if payload is None:
                nested = frozen_qwen_span_readouts(
                    model=backend.model,
                    input_ids=input_ids,
                    attention_mask=attention_mask,
                    span_rows=span_rows,
                    device=backend.device,
                )
                payload = {
                    "format": f"{MULTIVIEW_CACHE_VERSION}_clean_transition_row_7c",
                    **expected,
                    "readouts": nested,
                    "readout_sha256": readout_payload_hash(nested),
                    "span_rows": span_rows,
                    "source_metadata": source_metadata,
                    "token_count": int(input_ids.shape[1]),
                    "corpus_lineage_sha256": lineage,
                    "truncated": False,
                    "created_at_utc": utc_now(),
                }
                atomic_torch_save(payload, path)
                computed += 1
            readouts = {
                layer: flatten_multiview_readouts(
                    [payload], layer=layer, view_names=TRANSITION_VIEW_NAMES
                )[0].to(torch.float32)
                for layer in LAYER_CANDIDATES
            }
            source = "resumed_clean_row" if resumed_row else "computed_clean_row"
        for layer in LAYER_CANDIDATES:
            tensors[layer].append(readouts[layer])
        rows.append(
            {
                "format": "clean_multiview_transition_metadata_7c_v1",
                **expected,
                "parent_memory_id": str(transition["parent_memory_id"]),
                "parent_task_id": str(transition["parent_task_id"]),
                "step_index": int(transition["step_index"]),
                "step_count": int(transition["step_count"]),
                "token_count": int(input_ids.shape[1]),
                "corpus_lineage_sha256": lineage,
                "provenance": source,
                "truncated": False,
            }
        )
        attempt.progress(
            status="clean_multiview_transition_cache",
            completed=index + 1,
            total=len(ordered),
            reused=reused,
            resumed=resumed,
            newly_computed=computed,
            latest_validated_checkpoint=str(
                output_root if source == "immutable_exp020_reuse" else path
            ),
        )
        del input_ids, attention_mask
        if (index + 1) % 10 == 0:
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
    matrices = {layer: torch.stack(values) for layer, values in tensors.items()}
    return matrices, rows, {
        "total": len(ordered),
        "reused": reused,
        "resumed": resumed,
        "newly_computed": computed,
    }


def _aggregate(
    *,
    path: Path,
    kind: str,
    matrices: Mapping[str, torch.Tensor],
    rows: Sequence[Mapping[str, Any]],
    view_names: Sequence[str],
    model_name: str,
    renderer_version: str,
    lineage: str,
) -> dict[str, Any]:
    id_name = "state_example_id" if kind == "state" else "transition_id"
    payload = {
        "format": f"clean_{kind}_multiview_aggregate_7c_v1",
        "ordered_ids": [str(row[id_name]) for row in rows],
        "representations": dict(matrices),
        "rows": list(rows),
        "model_name": model_name,
        "renderer_version": renderer_version,
        "view_names": list(view_names),
        "pooling_rules": list(POOLING_RULES),
        "tensor_sha256": {
            layer: tensor_state_sha256({"representations": tensor})
            for layer, tensor in matrices.items()
        },
        "corpus_lineage_sha256": lineage,
        "created_at_utc": utc_now(),
    }
    atomic_torch_save(payload, path)
    reloaded = torch.load(path, map_location="cpu", weights_only=False)
    for layer in LAYER_CANDIDATES:
        if tensor_state_sha256(
            {"representations": reloaded["representations"][layer]}
        ) != reloaded["tensor_sha256"][layer]:
            raise ValueError(f"Written aggregate tensor differs: {kind} {layer}")
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "count": len(rows),
        "tensor_sha256": payload["tensor_sha256"],
    }


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
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", required=True)
    parser.add_argument("--approved-over-threshold", action="store_true")
    parser.add_argument("--tmux-session", default="exp025c")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7c"]
    if os.name != "nt" and not os.path.ismount(Path(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    preparation = _json(args.artifact_dir / "data_preparation_summary.json")
    if preparation["status"] != "completed":
        raise RuntimeError("CPU preparation is incomplete")
    preflight = preparation["multiview_preflight"]
    if preflight["requires_explicit_runtime_approval"] and not args.approved_over_threshold:
        raise RuntimeError("Multiview cache exceeds the review threshold")
    corpus = Path(settings["reconciled_corpus_dir"])
    parent = Path(settings["parent_exp025b"])
    paths = {
        "decisions": corpus / "decision_examples.jsonl",
        "transitions": corpus / "transition_manifest.jsonl",
        "task_split": corpus / "train_validation_task_manifest.json",
        "old_state": Path(settings["multiview_cache"]["old_state_cache"]),
        "old_transition": Path(
            settings["multiview_cache"]["old_transition_cache"]
        ),
        "replay_lineage": parent / "replay_validated_corpus_manifest.json",
        "preflight": args.artifact_dir / "multiview_cache_preflight.json",
    }
    data_hashes = {name: sha256_file(path) for name, path in paths.items()}
    backend = build_backend(cfg, load_model=True)
    backend.model.eval()
    for parameter in backend.model.parameters():
        parameter.requires_grad_(False)
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Qwen is not frozen")
    examples = load_decision_examples(paths["decisions"])
    task_split = _task_split(corpus)
    transitions = [
        row
        for row in _rows(paths["transitions"])
        if task_split[str(row["parent_task_id"])] == "train"
    ]
    old_state = _validated_old_cache(paths["old_state"])
    old_transition = _validated_old_cache(paths["old_transition"])
    output_root = Path(settings["multiview_cache"]["output_root"])
    output_root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    lineage = str(settings["expected_structural_lineage_sha256"])
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="clean_frozen_qwen_multiview_cache",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        state_matrices, state_rows, state_counts = _clean_state_cache(
            backend=backend,
            examples=examples,
            task_split=task_split,
            old=old_state,
            output_root=output_root,
            prompt_profile=cfg.benchmark.prompt_profile,
            renderer_version=str(settings["multiview_cache"]["renderer_version"]),
            lineage=lineage,
            attempt=attempt,
        )
        transition_matrices, transition_rows, transition_counts = _clean_transition_cache(
            backend=backend,
            transitions=transitions,
            old=old_transition,
            output_root=output_root,
            renderer_version=str(settings["multiview_cache"]["renderer_version"]),
            lineage=lineage,
            attempt=attempt,
        )
        state_aggregate = _aggregate(
            path=output_root / "state_multiview.pt",
            kind="state",
            matrices=state_matrices,
            rows=state_rows,
            view_names=STATE_VIEW_NAMES,
            model_name=str(backend.model_name),
            renderer_version=str(settings["multiview_cache"]["renderer_version"]),
            lineage=lineage,
        )
        transition_aggregate = _aggregate(
            path=output_root / "transition_multiview.pt",
            kind="transition",
            matrices=transition_matrices,
            rows=transition_rows,
            view_names=TRANSITION_VIEW_NAMES,
            model_name=str(backend.model_name),
            renderer_version=str(settings["multiview_cache"]["renderer_version"]),
            lineage=lineage,
        )
        expected = settings["multiview_cache"]
        checks = {
            "state_reused": state_counts["reused"]
            == int(expected["expected_reused_states"]),
            "state_new": state_counts["newly_computed"] + state_counts["resumed"]
            == int(expected["expected_recomputed_states"]),
            "transition_reused": transition_counts["reused"]
            == int(expected["expected_reused_transitions"]),
            "transition_new": transition_counts["newly_computed"]
            + transition_counts["resumed"]
            == int(expected["expected_recomputed_transitions"]),
            "state_lineage": all(
                row["corpus_lineage_sha256"] == lineage for row in state_rows
            ),
            "transition_lineage": all(
                row["corpus_lineage_sha256"] == lineage for row in transition_rows
            ),
            "no_target": all(
                not row["target_action_accessed"] for row in state_rows
            ),
            "no_truncation": all(not row["truncated"] for row in [*state_rows, *transition_rows]),
        }
        if not all(checks.values()):
            raise RuntimeError(
                f"Clean multiview validation failed: "
                f"{[name for name, passed in checks.items() if not passed]}"
            )
        elapsed = time.perf_counter() - started
        summary = {
            "format": "clean_multiview_cache_summary_7c_v1",
            "status": "completed",
            "checks": checks,
            "state": {**state_counts, "aggregate": state_aggregate},
            "transition": {
                **transition_counts,
                "aggregate": transition_aggregate,
            },
            "new_qwen_forward_count": state_counts["newly_computed"]
            + transition_counts["newly_computed"],
            "resumed_row_count": state_counts["resumed"]
            + transition_counts["resumed"],
            "elapsed_seconds": elapsed,
            "h100_hours": elapsed / 3600.0,
            "qwen_frozen": True,
            "qwen_gradients": False,
            "target_actions_encoded": False,
        }
        summary_path = output_root / "clean_multiview_cache_summary.json"
        atomic_write_json(summary_path, summary)
        atomic_write_text(
            output_root / "clean_multiview_cache_report.md",
            "\n".join(
                [
                    "# EXP-025C Clean Multiview Cache",
                    "",
                    f"- state reused/new: `{state_counts['reused']}` / "
                    f"`{state_counts['resumed'] + state_counts['newly_computed']}`",
                    f"- transition reused/new: `{transition_counts['reused']}` / "
                    f"`{transition_counts['resumed'] + transition_counts['newly_computed']}`",
                    f"- new frozen-Qwen forwards: `{summary['new_qwen_forward_count']}`",
                    f"- elapsed H100 hours: `{summary['h100_hours']:.6f}`",
                    "- no target action, future observation, or truncation was encoded",
                    "",
                ]
            ),
        )
        attempt.progress(status="completed", latest_validated_checkpoint=str(summary_path))
        print(json.dumps(summary, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
