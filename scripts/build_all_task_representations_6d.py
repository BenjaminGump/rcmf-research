from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import statistics
import time
from typing import Any, Mapping

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.training.cross_encoder_6c import (
    CROSS_ENCODER_CACHE_VERSION,
    CROSS_ENCODER_VIEW_NAMES,
    cross_encoder_prompt_and_char_spans,
    cross_encoder_tensor_hash,
    frozen_qwen_cross_encoder_readouts,
)
from rcmf.training.datasets import load_decision_examples
from rcmf.training.multiview_representations_6c import (
    LAYER_CANDIDATES,
    MULTIVIEW_CACHE_VERSION,
    POOLING_RULES,
    STATE_VIEW_NAMES,
    TRANSITION_VIEW_NAMES,
    flatten_multiview_readouts,
    frozen_qwen_span_readouts,
    multiview_geometry,
    query_state_text_and_char_spans,
    readout_payload_hash,
    tokenize_and_validate_char_spans,
)
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.oracle_convergence_5fb import tensor_state_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger, utc_now
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _step_bucket(step: int, count: int) -> str:
    ratio = (int(step) - 1) / max(int(count) - 1, 1)
    return "early" if ratio < 1 / 3 else "middle" if ratio < 2 / 3 else "late"


def _expanded_view_names(names: tuple[str, ...]) -> list[str]:
    return [f"{view}/{pool}" for view in names for pool in POOLING_RULES]


def _state_multiview_cache(
    *,
    backend: Any,
    examples: list[Any],
    query_manifest: Mapping[str, Any],
    preflight_rows: list[dict[str, Any]],
    prompt_profile: str,
    renderer_version: str,
    old_cache_path: Path,
    output_root: Path,
    attempt: AttemptLedger,
) -> tuple[dict[str, torch.Tensor], dict[str, Any], dict[str, Any]]:
    old = torch.load(old_cache_path, map_location="cpu", weights_only=False)
    if old["renderer_version"] != renderer_version or old["model_name"] != backend.model_name:
        raise ValueError("Immutable EXP-019 state multi-view identity differs")
    old_position = {str(value): index for index, value in enumerate(old["ordered_ids"])}
    old_rows = {str(row["state_example_id"]): row for row in old["rows"]}
    prompt_hashes: dict[str, set[str]] = {}
    for row in preflight_rows:
        prompt_hashes.setdefault(str(row["state_example_id"]), set()).add(
            str(row["base_prompt_sha256"])
        )
    row_dir = output_root / "state_rows"
    row_dir.mkdir(parents=True, exist_ok=True)
    tensors: dict[str, list[torch.Tensor]] = {layer: [] for layer in LAYER_CANDIDATES}
    metadata_rows = []
    reused = 0
    computed = 0
    span_count = 0
    aligned_span_count = 0
    for position, query in enumerate(query_manifest["query_rows"], start=1):
        state_id = str(query["state_example_id"])
        example = examples[int(query["example_index"])]
        rendered, char_spans, source_metadata = query_state_text_and_char_spans(
            backend.tokenizer, example, prompt_profile
        )
        prompt_hash = sha256_text(rendered)
        if prompt_hashes.get(state_id) != {prompt_hash}:
            raise ValueError(f"Expanded state prompt hash differs for {state_id}")
        input_ids, attention_mask, span_rows = tokenize_and_validate_char_spans(
            backend.tokenizer, rendered, char_spans
        )
        if int(input_ids.shape[1]) != int(query["prompt_tokens"]):
            raise ValueError(f"Expanded state token count differs for {state_id}")
        span_count += len(span_rows)
        aligned_span_count += sum(
            bool(row["decoded_matches_aligned_source"]) for row in span_rows.values()
        )
        expected = {
            "format": f"{MULTIVIEW_CACHE_VERSION}_state_row",
            "state_example_id": state_id,
            "prompt_sha256": prompt_hash,
            "renderer_version": renderer_version,
            "model_name": str(backend.model_name),
        }
        metadata: dict[str, Any]
        if state_id in old_position:
            metadata = old_rows[state_id]
            if any(metadata.get(key) != value for key, value in expected.items()):
                raise ValueError(f"Immutable EXP-019 state row differs: {state_id}")
            for layer in LAYER_CANDIDATES:
                tensors[layer].append(old["representations"][layer][old_position[state_id]])
            reused += 1
        else:
            row_path = row_dir / f"{state_id.replace(':', '__')}.pt"
            payload = None
            if row_path.exists():
                candidate = torch.load(row_path, map_location="cpu", weights_only=False)
                if any(candidate.get(key) != value for key, value in expected.items()):
                    raise ValueError(f"Existing EXP-020 state row differs: {row_path}")
                if readout_payload_hash(candidate["readouts"]) != candidate["readout_sha256"]:
                    raise ValueError(f"Existing EXP-020 state readout hash differs: {row_path}")
                payload = candidate
                reused += 1
            if payload is None:
                readouts = frozen_qwen_span_readouts(
                    model=backend.model,
                    input_ids=input_ids,
                    attention_mask=attention_mask,
                    span_rows=span_rows,
                    device=backend.device,
                )
                payload = {
                    **expected,
                    "task_id": str(query["task_id"]),
                    "example_index": int(query["example_index"]),
                    "split": str(query["split"]),
                    "apps": [str(value) for value in query.get("apps", [])],
                    "step_id": int(query["step_id"]),
                    "step_count": int(query["step_count"]),
                    "step_bucket": _step_bucket(query["step_id"], query["step_count"]),
                    "token_count": int(input_ids.shape[1]),
                    "text_sha256": prompt_hash,
                    "span_rows": span_rows,
                    "source_metadata": source_metadata,
                    "readouts": readouts,
                    "readout_sha256": readout_payload_hash(readouts),
                    "model_config_commit_hash": getattr(
                        backend.model.config, "_commit_hash", None
                    ),
                    "target_action_accessed": False,
                    "future_observation_accessed": False,
                    "truncated": False,
                    "created_at_utc": utc_now(),
                }
                atomic_torch_save(payload, row_path)
                computed += 1
            metadata = {key: value for key, value in payload.items() if key != "readouts"}
            for layer in LAYER_CANDIDATES:
                tensors[layer].append(
                    flatten_multiview_readouts(
                        [payload], layer=layer, view_names=STATE_VIEW_NAMES
                    )[0]
                )
        metadata_rows.append(metadata)
        attempt.progress(
            status="encoding_exp020_multiview_states",
            completed=position,
            total=len(query_manifest["query_rows"]),
            reused=reused,
            newly_computed=computed,
            latest_validated_checkpoint=(
                str(old_cache_path) if state_id in old_position else str(row_path)
            ),
        )
        del input_ids, attention_mask
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    if aligned_span_count != span_count:
        raise ValueError("Not all state multi-view spans decode to aligned source text")
    ordered_ids = [str(row["state_example_id"]) for row in query_manifest["query_rows"]]
    matrices = {
        layer: torch.stack(values).to(torch.float32) for layer, values in tensors.items()
    }
    aggregate = {
        "format": f"{MULTIVIEW_CACHE_VERSION}_state_aggregate_6d",
        "model_name": str(backend.model_name),
        "renderer_version": renderer_version,
        "ordered_ids": ordered_ids,
        "view_names": list(STATE_VIEW_NAMES),
        "pooling_rules": list(POOLING_RULES),
        "representations": matrices,
        "rows": metadata_rows,
        "tensor_sha256": {
            layer: tensor_state_sha256({"representations": tensor})
            for layer, tensor in matrices.items()
        },
        "immutable_exp019_reused": len(old_position),
        "resumed_exp020_rows": reused - len(old_position),
        "newly_computed": computed,
        "created_at_utc": utc_now(),
    }
    aggregate_path = output_root / "state_multiview.pt"
    atomic_torch_save(aggregate, aggregate_path)
    metadata_by_id = {
        str(row["state_example_id"]): {
            "task_label": str(row["task_id"]),
            "app_label": "+".join(sorted(row.get("apps", []))) or "none",
            "step_bucket": _step_bucket(row["step_id"], row["step_count"]),
        }
        for row in query_manifest["query_rows"]
    }
    geometry = {
        layer: multiview_geometry(
            matrix,
            ordered_ids=ordered_ids,
            view_names=_expanded_view_names(STATE_VIEW_NAMES),
            metadata_by_id=metadata_by_id,
        )
        for layer, matrix in matrices.items()
    }
    report = {
        "format": "expanded_state_multiview_report_6d_v1",
        "state_count": len(ordered_ids),
        "immutable_exp019_reused": len(old_position),
        "resumed_exp020_rows": reused - len(old_position),
        "newly_computed": computed,
        "span_count": span_count,
        "aligned_span_count": aligned_span_count,
        "no_truncation": True,
        "target_action_accessed": False,
        "aggregate_path": str(aggregate_path),
        "aggregate_sha256": sha256_file(aggregate_path),
        "geometry": geometry,
    }
    return matrices, aggregate, report


def _transition_multiview_reuse(
    *,
    source_path: Path,
    panel_rows: list[dict[str, Any]],
    output_path: Path,
) -> tuple[dict[str, torch.Tensor], dict[str, Any], dict[str, Any]]:
    payload = torch.load(source_path, map_location="cpu", weights_only=False)
    expected_ids = sorted(str(row["transition_id"]) for row in panel_rows)
    if [str(value) for value in payload["ordered_ids"]] != expected_ids:
        raise ValueError("Immutable transition multi-view IDs differ")
    panel_by_id = {str(row["transition_id"]): row for row in panel_rows}
    for row in payload["rows"]:
        source = panel_by_id[str(row["transition_id"])]
        if row["transition_content_sha256"] != source["transition_content_sha256"]:
            raise ValueError("Immutable transition multi-view content hash differs")
        if row["teacher_section_sha256"] != source["teacher_section_sha256"]:
            raise ValueError("Immutable transition multi-view renderer hash differs")
    copied = {
        **payload,
        "format": f"{MULTIVIEW_CACHE_VERSION}_transition_aggregate_6d_reuse",
        "immutable_source_path": str(source_path),
        "immutable_source_sha256": sha256_file(source_path),
        "reused": len(expected_ids),
        "newly_computed": 0,
    }
    atomic_torch_save(copied, output_path)
    report = {
        "format": "expanded_transition_multiview_reuse_6d_v1",
        "passed": True,
        "transition_count": len(expected_ids),
        "source_path": str(source_path),
        "source_sha256": sha256_file(source_path),
        "output_path": str(output_path),
        "output_sha256": sha256_file(output_path),
        "newly_computed": 0,
    }
    return {
        key: value.to(torch.float32) for key, value in payload["representations"].items()
    }, copied, report


def _cross_encoder_cache(
    *,
    backend: Any,
    examples: list[Any],
    pair_rows: list[dict[str, Any]],
    preflight_rows: list[dict[str, Any]],
    panel_rows: list[dict[str, Any]],
    prompt_profile: str,
    renderer_version: str,
    old_root: Path,
    output_root: Path,
    source_commit: str,
    attempt: AttemptLedger,
) -> tuple[dict[str, torch.Tensor], dict[str, Any]]:
    old_aggregate_path = old_root / "cross_encoder_representations.pt"
    old = torch.load(old_aggregate_path, map_location="cpu", weights_only=False)
    old_position = {
        str(value): index for index, value in enumerate(old["ordered_pair_ids"])
    }
    preflight = {str(row["pair_id"]): row for row in preflight_rows}
    transitions = {str(row["transition_id"]): row for row in panel_rows}
    output_rows = output_root / "rows"
    output_rows.mkdir(parents=True, exist_ok=True)
    features: dict[str, torch.Tensor] = {}
    prompt_tokens = []
    span_counts = {name: [] for name in CROSS_ENCODER_VIEW_NAMES}
    immutable_reused = 0
    resumed = 0
    computed = 0
    model_commit = getattr(backend.model.config, "_commit_hash", None)
    ordered = sorted(pair_rows, key=lambda row: str(row["pair_id"]))
    for position, row in enumerate(ordered, start=1):
        pair_id = str(row["pair_id"])
        transition = transitions[str(row["transition_id"])]
        prompt, char_spans, source_metadata = cross_encoder_prompt_and_char_spans(
            backend.tokenizer,
            examples[int(row["state_index"])],
            transition,
            prompt_profile,
        )
        source = preflight[pair_id]
        prompt_hash = sha256_text(prompt)
        if prompt_hash != str(source["teacher_prompt_sha256"]):
            raise ValueError(f"Cross-encoder teacher prompt hash differs: {pair_id}")
        input_ids, attention_mask, span_rows = tokenize_and_validate_char_spans(
            backend.tokenizer, prompt, char_spans
        )
        token_count = int(input_ids.shape[1])
        if token_count != int(source["combined_prompt_tokens"]):
            raise ValueError(f"Cross-encoder prompt token count differs: {pair_id}")
        if token_count + int(row["target_tokens"]) > int(source["context_limit"]):
            raise ValueError(f"Cross-encoder pair exceeds context: {pair_id}")
        expected = {
            "format": f"{CROSS_ENCODER_CACHE_VERSION}_row",
            "pair_id": pair_id,
            "state_example_id": str(row["state_example_id"]),
            "transition_id": str(row["transition_id"]),
            "teacher_prompt_sha256": prompt_hash,
            "base_prompt_sha256": str(row["base_prompt_sha256"]),
            "transition_content_sha256": str(row["transition_content_sha256"]),
            "renderer_version": renderer_version,
            "teacher_renderer_version": str(source["renderer_version"]),
            "transition_renderer_version": str(source["transition_renderer_version"]),
            "model_name": str(backend.model_name),
            "model_config_commit_hash": model_commit,
        }
        payload = None
        if pair_id in old_position:
            old_path = old_root / "rows" / f"{sha256_text(pair_id)}.pt"
            candidate = torch.load(old_path, map_location="cpu", weights_only=False)
            if any(candidate.get(key) != value for key, value in expected.items()):
                raise ValueError(f"Immutable cross-encoder row differs: {pair_id}")
            old_tensor = old["representations"][old_position[pair_id]]
            if not torch.equal(candidate["representations"], old_tensor):
                raise ValueError(f"Immutable cross-encoder aggregate differs: {pair_id}")
            payload = candidate
            immutable_reused += 1
        else:
            row_path = output_rows / f"{sha256_text(pair_id)}.pt"
            if row_path.exists():
                candidate = torch.load(row_path, map_location="cpu", weights_only=False)
                if any(candidate.get(key) != value for key, value in expected.items()):
                    raise ValueError(f"Existing EXP-020 cross row differs: {pair_id}")
                if cross_encoder_tensor_hash(candidate["representations"]) != candidate[
                    "tensor_sha256"
                ]:
                    raise ValueError(f"Existing EXP-020 cross tensor hash differs: {pair_id}")
                payload = candidate
                resumed += 1
            if payload is None:
                values = frozen_qwen_cross_encoder_readouts(
                    model=backend.model,
                    input_ids=input_ids,
                    attention_mask=attention_mask,
                    span_rows=span_rows,
                    device=backend.device,
                ).to(torch.float16)
                payload = {
                    **expected,
                    "representations": values,
                    "tensor_sha256": cross_encoder_tensor_hash(values),
                    "view_names": list(CROSS_ENCODER_VIEW_NAMES),
                    "combined_prompt_tokens": token_count,
                    "target_tokens_not_encoded": int(row["target_tokens"]),
                    "context_limit": int(source["context_limit"]),
                    "span_rows": span_rows,
                    "source_metadata": source_metadata,
                    "target_action_accessed": False,
                    "future_observation_accessed": False,
                    "truncated": False,
                    "source_commit": source_commit,
                    "created_at_utc": utc_now(),
                }
                atomic_torch_save(payload, row_path)
                computed += 1
        features[pair_id] = payload["representations"].to(torch.float32).flatten()
        prompt_tokens.append(token_count)
        for name in CROSS_ENCODER_VIEW_NAMES:
            span_counts[name].append(int(span_rows[name]["token_count"]))
        attempt.progress(
            status="encoding_exp020_prompt_cross_encoder",
            completed=position,
            total=len(ordered),
            immutable_reused=immutable_reused,
            resumed=resumed,
            newly_computed=computed,
            latest_validated_checkpoint=(
                str(old_aggregate_path) if pair_id in old_position else str(row_path)
            ),
        )
        if position % 25 == 0:
            print(
                json.dumps(
                    {
                        "cross_completed": position,
                        "cross_total": len(ordered),
                        "immutable_reused": immutable_reused,
                        "resumed": resumed,
                        "newly_computed": computed,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
    matrix = torch.stack([features[str(row["pair_id"])] for row in ordered]).to(
        torch.float16
    )
    aggregate = {
        "format": f"{CROSS_ENCODER_CACHE_VERSION}_aggregate_6d",
        "ordered_pair_ids": [str(row["pair_id"]) for row in ordered],
        "representations": matrix,
        "tensor_sha256": cross_encoder_tensor_hash(matrix),
        "view_names": list(CROSS_ENCODER_VIEW_NAMES),
        "model_name": str(backend.model_name),
        "model_config_commit_hash": model_commit,
        "renderer_version": renderer_version,
        "immutable_exp019_reused": immutable_reused,
        "resumed_exp020_rows": resumed,
        "newly_computed": computed,
        "created_at_utc": utc_now(),
    }
    aggregate_path = output_root / "cross_encoder_representations.pt"
    atomic_torch_save(aggregate, aggregate_path)
    report = {
        "format": "expanded_prompt_cross_encoder_cache_report_6d_v1",
        "pair_count": len(ordered),
        "immutable_exp019_reused": immutable_reused,
        "resumed_exp020_rows": resumed,
        "newly_computed": computed,
        "aggregate_path": str(aggregate_path),
        "aggregate_sha256": sha256_file(aggregate_path),
        "aggregate_tensor_sha256": aggregate["tensor_sha256"],
        "old_aggregate_sha256": sha256_file(old_aggregate_path),
        "no_truncation": True,
        "target_action_accessed": False,
        "prompt_tokens": {
            "min": min(prompt_tokens),
            "mean": statistics.fmean(prompt_tokens),
            "max": max(prompt_tokens),
        },
        "span_token_counts": {
            name: {
                "min": min(values),
                "mean": statistics.fmean(values),
                "max": max(values),
            }
            for name, values in span_counts.items()
        },
    }
    return features, report


def _report(summary: Mapping[str, Any]) -> str:
    state = summary["state_multiview"]
    cross = summary["cross_encoder"]
    return "\n".join(
        [
            "# EXP-020 Expanded Frozen-Qwen Representation Caches",
            "",
            "## VERIFIED",
            "",
            f"- state multi-view rows: `{state['state_count']}`",
            f"- immutable state rows reused: `{state['immutable_exp019_reused']}`",
            f"- newly computed state rows: `{state['newly_computed']}`",
            f"- transition rows reused: `{summary['transition_multiview']['transition_count']}`",
            f"- cross-encoder rows: `{cross['pair_count']}`",
            f"- immutable cross rows reused: `{cross['immutable_exp019_reused']}`",
            f"- newly computed cross rows: `{cross['newly_computed']}`",
            f"- runtime: `{summary['runtime_seconds'] / 3600.0:.3f}` H100-hours",
            "",
            "No target action, future observation, truncation, or Qwen gradient was used.",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build EXP-020 expanded Qwen caches")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_all_task_interaction_6d.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp020")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6d"]
    run_manifest = _load_json(args.artifact_dir / "run_manifest.json")
    data_summary = _load_json(args.artifact_dir / "data_preparation_summary.json")
    if data_summary["status"] != "completed":
        raise ValueError("EXP-020 CPU data preparation is not complete")
    existing_attempt_ids = {
        str(row["attempt_id"])
        for row in read_jsonl(args.artifact_dir / "attempts.jsonl")
    }
    if args.attempt_id in existing_attempt_ids:
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="expanded_frozen_qwen_representation_caches",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=str(run_manifest["config_sha256"]),
        data_manifest_hashes=run_manifest["data_manifest_hashes"],
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        exp017 = Path(settings["exp017_artifact"])
        exp019 = Path(settings["exp019_artifact"])
        source_data = Path(settings["source_data"])
        query_manifest = _load_json(args.artifact_dir / "expanded_query_manifest.json")
        preflight_rows = _load_rows(args.artifact_dir / "pair_preflight.jsonl")
        pair_rows = _load_rows(args.artifact_dir / "two_axis_pair_rows.jsonl")
        panel_rows = _load_rows(exp017 / "transition_panel.jsonl")
        examples = load_decision_examples(source_data / "decision_examples.jsonl")
        backend = build_backend(cfg, load_model=True)
        backend.model.eval()
        for parameter in backend.model.parameters():
            parameter.requires_grad_(False)
        cache_root = args.artifact_dir / "representation_cache"
        multiview_root = cache_root / "multiview"
        multiview_root.mkdir(parents=True, exist_ok=True)
        _, _, state_report = _state_multiview_cache(
            backend=backend,
            examples=examples,
            query_manifest=query_manifest,
            preflight_rows=preflight_rows,
            prompt_profile=cfg.benchmark.prompt_profile,
            renderer_version=str(settings["multiview"]["renderer_version"]),
            old_cache_path=(
                exp019 / "parts_c_d/multiview_cache/state_multiview.pt"
            ),
            output_root=multiview_root,
            attempt=attempt,
        )
        _, _, transition_report = _transition_multiview_reuse(
            source_path=exp019 / "parts_c_d/multiview_cache/transition_multiview.pt",
            panel_rows=panel_rows,
            output_path=multiview_root / "transition_multiview.pt",
        )
        atomic_write_json(multiview_root / "state_multiview_report.json", state_report)
        atomic_write_json(
            multiview_root / "transition_multiview_report.json", transition_report
        )
        cross_root = cache_root / "cross_encoder"
        cross_root.mkdir(parents=True, exist_ok=True)
        _, cross_report = _cross_encoder_cache(
            backend=backend,
            examples=examples,
            pair_rows=pair_rows,
            preflight_rows=preflight_rows,
            panel_rows=panel_rows,
            prompt_profile=cfg.benchmark.prompt_profile,
            renderer_version=str(settings["cross_encoder"]["renderer_version"]),
            old_root=exp019 / "part_e/cross_encoder_cache",
            output_root=cross_root,
            source_commit=args.lambda_head,
            attempt=attempt,
        )
        atomic_write_json(cross_root / "cross_encoder_cache_report.json", cross_report)
        runtime_seconds = time.perf_counter() - started
        summary = {
            "format": "all_task_frozen_qwen_representation_summary_6d_v1",
            "status": "completed",
            "run_uuid": str(settings["run_uuid"]),
            "source_commit": args.lambda_head,
            "model_name": backend.model_name,
            "model_config_commit_hash": getattr(backend.model.config, "_commit_hash", None),
            "state_multiview": state_report,
            "transition_multiview": transition_report,
            "cross_encoder": cross_report,
            "runtime_seconds": runtime_seconds,
            "actual_h100_hours": runtime_seconds / 3600.0,
            "hard_scope": {
                "qwen_frozen": True,
                "qwen_gradients": False,
                "teacher_forced_behavioral_scoring": False,
                "target_action_accessed": False,
                "future_observation_accessed": False,
                "no_truncation": True,
                "behavioral_program_training": False,
                "injector_training": False,
                "selector_training": False,
            },
            "timestamp_utc": utc_now(),
        }
        atomic_write_json(args.artifact_dir / "representation_summary.json", summary)
        atomic_write_text(args.artifact_dir / "representation_report.md", _report(summary))
        attempt.progress(
            status="expanded_representation_caches_completed",
            latest_validated_checkpoint=str(args.artifact_dir / "representation_summary.json"),
        )
        print(json.dumps({"state": state_report, "cross": cross_report}, indent=2), flush=True)


if __name__ == "__main__":
    main()
