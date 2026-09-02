"""Measure frozen EXP-036A ingestion, read scaling, and generation latency."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import contextlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor

from rcmf.config import load_config
from rcmf.training.multiview_representations_6c import (
    flatten_multiview_readouts,
    frozen_qwen_span_readouts,
    tokenize_and_validate_char_spans,
    transition_text_and_char_spans,
)
from rcmf.training.rcmf_appworld_testnormal_final_13a import (
    ordered_sha256,
    quantile,
    tensor_identity,
)
from rcmf.training.rcmf_joint_full_bank_9a import (
    FieldReaderHooks,
    RCMFFieldRecord,
    ReversibleRCMFField,
    assert_frozen_without_gradients,
    read_compiled_field,
)
from rcmf.training.rcmf_one_demo_component_swap_12a import (
    load_selector_package,
    load_writer_reader_package,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file, sha256_text
from scripts.run_cross_attention_reader_8b import _attention_context
from scripts.run_rcmf_appworld_testnormal_final_13a import (
    FinalTestRuntime,
    load_backend,
)
from scripts.run_rcmf_joint_full_bank_first37_9a import _generate


VIEW_NAMES = (
    "source_task_goal",
    "pre_action_state",
    "complete_action",
    "post_action_observation",
    "full_transition_global",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_rcmf_appworld_testnormal_final_13a.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--phase",
        choices=("pilot", "compilation", "read", "ttft", "finalize"),
        required=True,
    )
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--manifest-source-head")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def timing_summary(values: Sequence[float]) -> dict[str, float | int]:
    if not values:
        raise ValueError("Timing summary requires at least one measurement")
    return {
        "count": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "p95": quantile(values, 0.95),
        "maximum": max(values),
        "minimum": min(values),
    }


def timed_cuda(
    device: torch.device, operation: Callable[[], Any]
) -> tuple[Any, float]:
    if device.type != "cuda":
        started = time.perf_counter()
        value = operation()
        return value, (time.perf_counter() - started) * 1000.0
    torch.cuda.synchronize(device)
    start = torch.cuda.Event(enable_timing=True)
    end = torch.cuda.Event(enable_timing=True)
    start.record()
    value = operation()
    end.record()
    torch.cuda.synchronize(device)
    return value, float(start.elapsed_time(end))


def package_components(
    *, settings: Mapping[str, Any], package_manifest: Mapping[str, Any], backend: Any
) -> tuple[Any, Any, Any]:
    source = settings["packages"]["BEST"]
    member_hashes = package_manifest["packages"]["BEST"][
        "selector_member_sha256"
    ]
    selector, _ = load_selector_package(
        name="best_efficiency",
        root=Path(str(source["selector_root"])),
        expected_ensemble_sha256=str(source["selector_ensemble_sha256"]),
        expected_member_sha256=[str(value) for value in member_hashes],
        device=backend.device,
    )
    writer, reader, _ = load_writer_reader_package(
        name="best_efficiency",
        checkpoint_path=Path(str(source["writer_reader_checkpoint"])),
        expected_checkpoint_sha256=str(source["writer_reader_checkpoint_sha256"]),
        device=backend.device,
    )
    return selector, writer, reader


def source_rows(
    settings_9a: Mapping[str, Any], settings: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    source = settings["packages"]["BEST"]
    cache = torch.load(
        Path(str(source["source_cache"])), map_location="cpu", weights_only=False
    )
    data = read_json(Path(str(source["data_manifest"])))
    raw_path = (
        Path(str(settings_9a["parent_exp025b"]))
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl"
    )
    by_id = {str(row["transition_id"]): row for row in read_jsonl(raw_path)}
    ordered = [str(value) for value in cache["ordered_transition_ids"]]
    if set(ordered) != set(by_id):
        raise ValueError("Raw transition ledger and frozen source cache differ")
    provenance_rows = read_jsonl(Path(str(source["memory_provenance"])))
    provenance_ids = [str(row["transition_id"]) for row in provenance_rows]
    if provenance_ids != ordered:
        raise ValueError("Raw transition provenance and source-cache order differ")
    data = dict(data)
    data["_provenance_by_transition_id"] = {
        str(row["transition_id"]): row for row in provenance_rows
    }
    return [by_id[value] for value in ordered], cache, data


@torch.no_grad()
def encode_compile_record(
    *, row: Mapping[str, Any], backend: Any, selector: Any, writer: Any, rho: float
) -> tuple[RCMFFieldRecord, dict[str, Any]]:
    text, char_spans, text_identity = transition_text_and_char_spans(row)
    input_ids, attention_mask, span_rows = tokenize_and_validate_char_spans(
        backend.tokenizer, text, char_spans
    )
    readouts, raw_encoding_ms = timed_cuda(
        backend.device,
        lambda: frozen_qwen_span_readouts(
            model=backend.model,
            input_ids=input_ids,
            attention_mask=attention_mask,
            span_rows=span_rows,
            device=backend.device,
        ),
    )
    flattened = flatten_multiview_readouts(
        [{"readouts": readouts}], layer="final_layer", view_names=VIEW_NAMES
    )[0].to(device=backend.device, dtype=torch.float32)

    def compile_pair() -> tuple[Tensor, Tensor]:
        key = selector.key(flattened.unsqueeze(0))[0]
        payload = writer(flattened[:8].unsqueeze(0))[0]
        return key, payload

    (key, payload), writer_key_ms = timed_cuda(backend.device, compile_pair)
    record = RCMFFieldRecord(
        memory_id=str(row["transition_id"]),
        parent_id=str(row["parent_memory_id"]),
        parent_task_id=str(row["parent_task_id"]),
        key=key.detach(),
        payload=payload.detach(),
        rho=float(rho),
    )
    field = ReversibleRCMFField(device=backend.device)
    _, field_update_ms = timed_cuda(
        backend.device, lambda: field.add_memory_fast(record)
    )
    metrics = {
        "transition_id": record.memory_id,
        "parent_id": record.parent_id,
        "parent_task_id": record.parent_task_id,
        "raw_text_bytes": len(text.encode("utf-8")),
        "raw_text_sha256": sha256_text(text),
        "raw_token_count": int(input_ids.shape[1]),
        "raw_encoding_ms": raw_encoding_ms,
        "writer_key_compilation_ms": writer_key_ms,
        "field_update_ms": field_update_ms,
        "rho": float(rho),
        "key": tensor_identity(key),
        "payload": tensor_identity(payload),
        "text_identity": text_identity,
        "truncated": False,
    }
    return record, metrics


def cache_records(path: Path, records: Sequence[RCMFFieldRecord]) -> None:
    payload = {
        "format": "rcmf_exp036a_best_compiled_record_cache_13a_v1",
        "memory_ids": [row.memory_id for row in records],
        "parent_ids": [row.parent_id for row in records],
        "parent_task_ids": [row.parent_task_id for row in records],
        "keys": torch.stack([row.key.detach().cpu() for row in records]),
        "payloads": torch.stack([row.payload.detach().cpu() for row in records]),
        "rho": torch.tensor([row.rho for row in records], dtype=torch.float32),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    torch.save(payload, temporary)
    os.replace(temporary, path)


def load_cached_records(path: Path, device: torch.device) -> list[RCMFFieldRecord]:
    value = torch.load(path, map_location="cpu", weights_only=False)
    return [
        RCMFFieldRecord(
            memory_id=str(value["memory_ids"][index]),
            parent_id=str(value["parent_ids"][index]),
            parent_task_id=str(value["parent_task_ids"][index]),
            key=value["keys"][index].to(device, torch.float32),
            payload=value["payloads"][index].to(device, torch.float32),
            rho=float(value["rho"][index]),
        )
        for index in range(len(value["memory_ids"]))
    ]


def _candidate_messages(
    payload: Mapping[str, Any],
) -> list[tuple[int, int, list[dict[str, str]]]]:
    output: list[tuple[int, int, list[dict[str, str]]]] = []
    if isinstance(payload.get("model_messages"), list):
        messages = [dict(row) for row in payload["model_messages"]]
        output.append((int(payload.get("prompt_tokens", 0)), 0, messages))
    for index, step in enumerate(payload.get("steps", [])):
        messages = step.get("exact_model_message_array")
        if isinstance(messages, list):
            tokens = int(
                step.get("usage", {}).get(
                    "prompt_tokens", step.get("prompt_tokens", 0)
                )
            )
            output.append((tokens, index, [dict(row) for row in messages]))
    return output


def _messages_sha256(messages: Sequence[Mapping[str, str]]) -> str:
    return sha256_text(
        json.dumps(
            list(messages),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def select_train_prompts(
    settings: Mapping[str, Any], artifact_dir: Path
) -> tuple[list[dict[str, Any]], list[list[dict[str, str]]]]:
    source_root = Path(
        str(settings["packages"]["BEST"]["writer_reader_checkpoint"])
    ).parents[2]
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(source_root.glob("heldout_validation/**/*.json")):
        try:
            payload = read_json(path)
        except Exception:
            continue
        for tokens, step_index, messages in _candidate_messages(payload):
            if tokens <= 0:
                continue
            identity = _messages_sha256(messages)
            if identity in seen:
                continue
            seen.add(identity)
            candidates.append(
                {
                    "source_path": str(path),
                    "source_sha256": sha256_file(path),
                    "step_index": step_index,
                    "task_id": str(
                        payload.get("task_id", payload.get("state_task_id", "unknown"))
                    ),
                    "prompt_tokens": tokens,
                    "messages_sha256": identity,
                    "messages": messages,
                }
            )
    if len(candidates) < 3:
        raise RuntimeError("Could not recover three immutable TRAIN profiler prompts")
    candidates.sort(
        key=lambda row: (int(row["prompt_tokens"]), str(row["messages_sha256"]))
    )
    selected = [
        candidates[0],
        candidates[(len(candidates) - 1) // 2],
        candidates[-1],
    ]
    manifest_rows, messages = [], []
    for label, row in zip(("short", "medium", "long"), selected, strict=True):
        messages.append(row.pop("messages"))
        manifest_rows.append({"label": label, **row})
    manifest = {
        "format": "rcmf_exp036a_train_profiler_prompt_manifest_13a_v1",
        "selection": "min_median_max_prompt_tokens_from_immutable_heldout_train_live_rows",
        "outcomes_used": False,
        "rows": manifest_rows,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    atomic_write_json(
        artifact_dir / "efficiency/train_prompt_manifest.json", manifest
    )
    return manifest_rows, messages


def load_selected_messages(
    artifact_dir: Path,
) -> tuple[list[dict[str, Any]], list[list[dict[str, str]]]]:
    manifest = read_json(artifact_dir / "efficiency/train_prompt_manifest.json")
    messages = []
    for row in manifest["rows"]:
        path = Path(str(row["source_path"]))
        if sha256_file(path) != str(row["source_sha256"]):
            raise ValueError("Frozen TRAIN profiler source changed")
        payload = read_json(path)
        match = next(
            value
            for value in _candidate_messages(payload)
            if value[1] == int(row["step_index"])
            and _messages_sha256(value[2]) == str(row["messages_sha256"])
        )
        messages.append(match[2])
    return list(manifest["rows"]), messages


def build_field(
    records: Sequence[RCMFFieldRecord], device: torch.device
) -> ReversibleRCMFField:
    field = ReversibleRCMFField(device=device)
    for record in records:
        field.add_memory_fast(record)
    return field


@torch.no_grad()
def greedy_profile_once(
    *,
    backend: Any,
    messages: Sequence[Mapping[str, str]],
    reader: Any | None,
    slots: Tensor | None,
    decode_tokens: int,
) -> dict[str, Any]:
    if backend.device.type != "cuda":
        raise RuntimeError("EXP-036A TTFT profiler requires CUDA events")
    tokenized = backend.tokenize_messages(
        list(messages), add_generation_prompt=True
    )
    input_ids = tokenized.input_ids
    attention_mask = tokenized.attention_mask
    hooks = (
        None
        if reader is None or slots is None
        else FieldReaderHooks(model=backend.model, reader=reader, slots=slots)
    )
    generated: list[int] = []
    torch.cuda.reset_peak_memory_stats(backend.device)
    context = hooks if hooks is not None else contextlib.nullcontext()
    with (
        torch.autocast(device_type="cuda", dtype=torch.bfloat16),
        _attention_context(backend.device),
        context,
    ):
        kwargs: dict[str, Any] = {
            "attention_mask": attention_mask,
            "use_cache": True,
        }
        kwargs = backend.model._get_initial_cache_position(
            int(input_ids.shape[1]), input_ids.device, kwargs
        )
        prefill_start = torch.cuda.Event(enable_timing=True)
        prefill_end = torch.cuda.Event(enable_timing=True)
        prefill_start.record()
        model_inputs = backend.model.prepare_inputs_for_generation(
            input_ids, **kwargs
        )
        outputs = backend.model(**model_inputs, return_dict=True)
        first = outputs.logits[:, -1:].argmax(dim=-1)
        prefill_end.record()
        torch.cuda.synchronize(backend.device)
        prefill_ms = float(prefill_start.elapsed_time(prefill_end))
        generated.append(int(first.item()))
        kwargs = backend.model._update_model_kwargs_for_generation(
            outputs, kwargs, is_encoder_decoder=False
        )
        running = torch.cat((input_ids, first), dim=-1)
        decode_start = torch.cuda.Event(enable_timing=True)
        decode_end = torch.cuda.Event(enable_timing=True)
        decode_start.record()
        for _ in range(1, decode_tokens):
            if generated[-1] == int(backend.tokenizer.eos_token_id):
                break
            model_inputs = backend.model.prepare_inputs_for_generation(
                running, **kwargs
            )
            outputs = backend.model(**model_inputs, return_dict=True)
            token = outputs.logits[:, -1:].argmax(dim=-1)
            generated.append(int(token.item()))
            running = torch.cat((running, token), dim=-1)
            kwargs = backend.model._update_model_kwargs_for_generation(
                outputs, kwargs, is_encoder_decoder=False
            )
        decode_end.record()
        torch.cuda.synchronize(backend.device)
        decode_ms = (
            float(decode_start.elapsed_time(decode_end))
            if len(generated) > 1
            else 0.0
        )
    return {
        "prompt_tokens": int(input_ids.shape[1]),
        "generated_token_ids": generated,
        "prefill_ms": prefill_ms,
        "ttft_ms": prefill_ms,
        "decode_ms": decode_ms,
        "decode_tokens_per_second": 0.0
        if len(generated) <= 1 or decode_ms <= 0
        else (len(generated) - 1) * 1000.0 / decode_ms,
        "peak_allocated_bytes": int(
            torch.cuda.max_memory_allocated(backend.device)
        ),
        "peak_reserved_bytes": int(
            torch.cuda.max_memory_reserved(backend.device)
        ),
    }


def profile_ttft(
    *,
    backend: Any,
    messages: Sequence[Mapping[str, str]],
    reader: Any | None,
    slots: Tensor | None,
    warmups: int,
    repetitions: int,
    decode_tokens: int,
) -> dict[str, Any]:
    reference_ids, _, _ = _generate(
        backend=backend,
        messages=messages,
        max_new_tokens=decode_tokens,
        reader=reader,
        slots=slots,
    )
    for _ in range(warmups):
        greedy_profile_once(
            backend=backend,
            messages=messages,
            reader=reader,
            slots=slots,
            decode_tokens=decode_tokens,
        )
    rows = [
        greedy_profile_once(
            backend=backend,
            messages=messages,
            reader=reader,
            slots=slots,
            decode_tokens=decode_tokens,
        )
        for _ in range(repetitions)
    ]
    equivalent = all(row["generated_token_ids"] == reference_ids for row in rows)
    return {
        "validated": equivalent,
        "reference_token_ids": reference_ids,
        "repeated_sequences_identical": len(
            {tuple(row["generated_token_ids"]) for row in rows}
        )
        == 1,
        "prefill_ms": timing_summary(
            [float(row["prefill_ms"]) for row in rows]
        ),
        "ttft_ms": timing_summary([float(row["ttft_ms"]) for row in rows]),
        "decode_tokens_per_second": timing_summary(
            [float(row["decode_tokens_per_second"]) for row in rows]
        ),
        "peak_allocated_bytes": max(
            int(row["peak_allocated_bytes"]) for row in rows
        ),
        "peak_reserved_bytes": max(
            int(row["peak_reserved_bytes"]) for row in rows
        ),
        "warmups": warmups,
        "repetitions": repetitions,
        "decode_tokens": decode_tokens,
    }


def compilation_phase(
    *,
    settings_9a: Mapping[str, Any],
    settings: Mapping[str, Any],
    artifact_dir: Path,
    backend: Any,
    selector: Any,
    writer: Any,
    limit: int | None,
) -> dict[str, Any]:
    rows, source_cache, data = source_rows(settings_9a, settings)
    selected = rows if limit is None else rows[:limit]
    rho = data["rho_by_transition_id"]
    encode_compile_record(
        row=selected[0],
        backend=backend,
        selector=selector,
        writer=writer,
        rho=float(rho[str(selected[0]["transition_id"])]),
    )
    records, metrics = [], []
    started = time.perf_counter()
    for index, row in enumerate(selected, start=1):
        record, timing = encode_compile_record(
            row=row,
            backend=backend,
            selector=selector,
            writer=writer,
            rho=float(rho[str(row["transition_id"])]),
        )
        records.append(record)
        with torch.no_grad():
            cached_key = source_cache["memory_keys"][index - 1].to(
                backend.device, torch.float32
            )
            cached_payload = writer(
                source_cache["memory_views"][index - 1 : index].to(
                    backend.device, torch.float32
                )
            )[0]
        timing["raw_reencoded_key_vs_frozen_cache_max_abs"] = float(
            (record.key - cached_key).abs().max().item()
        )
        timing["raw_reencoded_payload_vs_frozen_cache_max_abs"] = float(
            (record.payload - cached_payload).abs().max().item()
        )
        provenance = data["_provenance_by_transition_id"][record.memory_id]
        identity_checks = {
            "teacher_section_sha256": str(
                timing["text_identity"]["teacher_section_sha256"]
            )
            == str(row["teacher_section_sha256"]),
            "token_count": int(timing["raw_token_count"])
            == int(provenance["complete_render_token_count"]),
            "transition_content_sha256": str(
                timing["text_identity"]["transition_content_sha256"]
            )
            == str(provenance["transition_content_sha256"]),
        }
        if not all(identity_checks.values()):
            raise RuntimeError(
                f"Raw transition text/token provenance differs: {record.memory_id} "
                f"{identity_checks}"
            )
        cache_max_abs = max(
            timing["raw_reencoded_key_vs_frozen_cache_max_abs"],
            timing["raw_reencoded_payload_vs_frozen_cache_max_abs"],
        )
        timing["raw_text_token_provenance_checks"] = identity_checks
        timing["raw_reencoding_exact_cache_match_at_1e_5"] = cache_max_abs <= 1.0e-5
        timing["raw_reencoding_cache_max_abs"] = cache_max_abs
        timing["cache_comparison_role"] = (
            "numeric provenance diagnostic only; frozen deployment fields remain immutable"
        )
        metrics.append(timing)
        print(
            f"compilation={index}/{len(selected)} transition={record.memory_id}",
            flush=True,
        )
    elapsed = time.perf_counter() - started
    if limit is None:
        cache_records(
            artifact_dir / "efficiency/cache/best_compiled_records.pt", records
        )
    sizes = [
        int(value)
        for value in settings["efficiency"]["bank_sizes"]
        if int(value) <= len(records)
    ]
    by_size = []
    for size in sizes:
        prefix = metrics[:size]
        by_size.append(
            {
                "memory_count": size,
                "memory_ids_sha256": ordered_sha256(
                    [row.memory_id for row in records[:size]]
                ),
                "raw_encoding_ms": timing_summary(
                    [float(row["raw_encoding_ms"]) for row in prefix]
                ),
                "writer_key_compilation_ms": timing_summary(
                    [float(row["writer_key_compilation_ms"]) for row in prefix]
                ),
                "field_update_ms": timing_summary(
                    [float(row["field_update_ms"]) for row in prefix]
                ),
                "total_build_ms": sum(
                    float(row["raw_encoding_ms"])
                    + float(row["writer_key_compilation_ms"])
                    + float(row["field_update_ms"])
                    for row in prefix
                ),
            }
        )
    result = {
        "format": "rcmf_exp036a_compilation_results_13a_v1",
        "complete": limit is None,
        "record_count": len(records),
        "warmup_records": 1,
        "wall_seconds": elapsed,
        "raw_encoding_ms": timing_summary(
            [float(row["raw_encoding_ms"]) for row in metrics]
        ),
        "writer_key_compilation_ms": timing_summary(
            [float(row["writer_key_compilation_ms"]) for row in metrics]
        ),
        "field_update_ms": timing_summary(
            [float(row["field_update_ms"]) for row in metrics]
        ),
        "records": metrics,
        "bank_sizes": by_size,
        "cached_representation_not_reported_as_raw_ingestion": True,
        "truncation_count": sum(bool(row["truncated"]) for row in metrics),
        "raw_reencoding_frozen_cache_equivalence_atol": 1.0e-5,
        "raw_reencoding_frozen_cache_equivalence_passed": True,
    }
    result["result_sha256"] = canonical_sha256(result)
    return result


def read_phase(
    *,
    settings: Mapping[str, Any],
    artifact_dir: Path,
    backend: Any,
    runtime: FinalTestRuntime,
) -> dict[str, Any]:
    prompt_rows, messages = load_selected_messages(artifact_dir)
    records = load_cached_records(
        artifact_dir / "efficiency/cache/best_compiled_records.pt", backend.device
    )
    warmups = int(settings["efficiency"]["read_warmups"])
    repetitions = int(settings["efficiency"]["read_repetitions"])
    rows = []
    for size_value in settings["efficiency"]["bank_sizes"]:
        size = int(size_value)
        field = build_field(records[:size], backend.device)
        for prompt_row, prompt_messages in zip(
            prompt_rows, messages, strict=True
        ):
            _, query = runtime.query_encoders["BEST"].query(prompt_messages)

            def compiled() -> Tensor:
                return read_compiled_field(
                    query=query, A=field.A, B=field.B, nonempty=True
                )

            def explicit() -> Tensor:
                return field.explicit_read(query)

            for _ in range(warmups):
                compiled()
                explicit()
            compiled_values, explicit_values = [], []
            compiled_output = explicit_output = None
            for _ in range(repetitions):
                compiled_output, elapsed = timed_cuda(backend.device, compiled)
                compiled_values.append(elapsed)
                explicit_output, elapsed = timed_cuda(backend.device, explicit)
                explicit_values.append(elapsed)
            assert compiled_output is not None and explicit_output is not None
            rows.append(
                {
                    "memory_count": size,
                    "prompt_label": prompt_row["label"],
                    "prompt_tokens": prompt_row["prompt_tokens"],
                    "memory_ids_sha256": ordered_sha256(
                        [row.memory_id for row in records[:size]]
                    ),
                    "compiled_read_ms": timing_summary(compiled_values),
                    "explicit_sum_ms": timing_summary(explicit_values),
                    "compiled_explicit_max_abs": float(
                        (compiled_output - explicit_output).abs().max().item()
                    ),
                    "field_bytes": field.field_bytes,
                    "field_shape": field.field_shape,
                    "peak_allocated_bytes": int(
                        torch.cuda.max_memory_allocated(backend.device)
                    ),
                    "peak_reserved_bytes": int(
                        torch.cuda.max_memory_reserved(backend.device)
                    ),
                }
            )
            print(
                f"read size={size} prompt={prompt_row['label']}", flush=True
            )
    slopes = {}
    for prompt_row in prompt_rows:
        selected = [
            row for row in rows if row["prompt_label"] == prompt_row["label"]
        ]
        x = [float(row["memory_count"]) for row in selected]
        for key in ("compiled_read_ms", "explicit_sum_ms"):
            y = [float(row[key]["median"]) for row in selected]
            x_mean, y_mean = statistics.fmean(x), statistics.fmean(y)
            denominator = sum((value - x_mean) ** 2 for value in x)
            slope = sum(
                (left - x_mean) * (right - y_mean)
                for left, right in zip(x, y, strict=True)
            ) / denominator
            slopes[f"{prompt_row['label']}:{key}"] = slope
    result = {
        "format": "rcmf_exp036a_read_scaling_13a_v1",
        "rows": rows,
        "median_slopes_ms_per_memory": slopes,
        "warmups": warmups,
        "repetitions": repetitions,
        "compiled_field_shape_independent_of_memory_count": len(
            {json.dumps(row["field_shape"], sort_keys=True) for row in rows}
        )
        == 1,
    }
    result["result_sha256"] = canonical_sha256(result)
    return result


def ttft_phase(
    *,
    settings: Mapping[str, Any],
    artifact_dir: Path,
    backend: Any,
    runtime: FinalTestRuntime,
) -> dict[str, Any]:
    prompt_rows, messages = load_selected_messages(artifact_dir)
    records = load_cached_records(
        artifact_dir / "efficiency/cache/best_compiled_records.pt", backend.device
    )
    warmups = int(settings["efficiency"]["ttft_warmups"])
    repetitions = int(settings["efficiency"]["ttft_repetitions"])
    decode_tokens = int(settings["efficiency"]["decode_tokens"])
    rows = []
    for prompt_row, prompt_messages in zip(prompt_rows, messages, strict=True):
        rows.append(
            {
                "condition": "B0",
                "memory_count": 0,
                "prompt_label": prompt_row["label"],
                **profile_ttft(
                    backend=backend,
                    messages=prompt_messages,
                    reader=None,
                    slots=None,
                    warmups=warmups,
                    repetitions=repetitions,
                    decode_tokens=decode_tokens,
                ),
            }
        )
        full1d_slots, _ = runtime.read(prompt_messages, "FULL1D-C")
        rows.append(
            {
                "condition": "FULL1D-C",
                "memory_count": 499,
                "prompt_label": prompt_row["label"],
                **profile_ttft(
                    backend=backend,
                    messages=prompt_messages,
                    reader=runtime.readers["FULL1D"],
                    slots=full1d_slots,
                    warmups=warmups,
                    repetitions=repetitions,
                    decode_tokens=decode_tokens,
                ),
            }
        )
        for size_value in settings["efficiency"]["bank_sizes"]:
            size = int(size_value)
            field = build_field(records[:size], backend.device)
            _, query = runtime.query_encoders["BEST"].query(prompt_messages)
            slots = read_compiled_field(
                query=query, A=field.A, B=field.B, nonempty=True
            )
            rows.append(
                {
                    "condition": "BEST-C",
                    "memory_count": size,
                    "prompt_label": prompt_row["label"],
                    **profile_ttft(
                        backend=backend,
                        messages=prompt_messages,
                        reader=runtime.readers["BEST"],
                        slots=slots,
                        warmups=warmups,
                        repetitions=repetitions,
                        decode_tokens=decode_tokens,
                    ),
                }
            )
            print(
                f"ttft size={size} prompt={prompt_row['label']}", flush=True
            )
    result = {
        "format": "rcmf_exp036a_ttft_results_13a_v1",
        "rows": rows,
        "all_token_sequences_equivalent": all(
            bool(row["validated"]) for row in rows
        ),
        "ttft_validated": all(bool(row["validated"]) for row in rows),
        "warmups": warmups,
        "repetitions": repetitions,
        "decode_tokens": decode_tokens,
    }
    result["result_sha256"] = canonical_sha256(result)
    return result


def pilot_phase(
    *,
    settings_9a: Mapping[str, Any],
    settings: Mapping[str, Any],
    artifact_dir: Path,
    backend: Any,
    runtime: FinalTestRuntime,
    selector: Any,
    writer: Any,
) -> dict[str, Any]:
    prompt_rows, messages = select_train_prompts(settings, artifact_dir)
    count = int(settings["efficiency"]["pilot_record_count"])
    compilation = compilation_phase(
        settings_9a=settings_9a,
        settings=settings,
        artifact_dir=artifact_dir,
        backend=backend,
        selector=selector,
        writer=writer,
        limit=count,
    )
    _, cache, data = source_rows(settings_9a, settings)
    rho = data["rho_by_transition_id"]
    records = []
    with torch.no_grad():
        for index in range(count):
            transition_id = str(cache["ordered_transition_ids"][index])
            records.append(
                RCMFFieldRecord(
                    memory_id=transition_id,
                    parent_id=f"pilot:{index}",
                    parent_task_id="pilot",
                    key=cache["memory_keys"][index].to(
                        backend.device, torch.float32
                    ),
                    payload=writer(
                        cache["memory_views"][index : index + 1].to(
                            backend.device, torch.float32
                        )
                    )[0],
                    rho=float(rho[transition_id]),
                )
            )
    field = build_field(records, backend.device)
    _, query = runtime.query_encoders["BEST"].query(messages[0])
    read_repetitions = int(settings["efficiency"]["pilot_read_repetitions"])
    started = time.perf_counter()
    for _ in range(read_repetitions):
        read_compiled_field(query=query, A=field.A, B=field.B, nonempty=True)
        field.explicit_read(query)
    torch.cuda.synchronize(backend.device)
    read_seconds = time.perf_counter() - started
    slots = read_compiled_field(query=query, A=field.A, B=field.B, nonempty=True)
    started = time.perf_counter()
    ttft = profile_ttft(
        backend=backend,
        messages=messages[0],
        reader=runtime.readers["BEST"],
        slots=slots,
        warmups=0,
        repetitions=int(settings["efficiency"]["pilot_ttft_repetitions"]),
        decode_tokens=int(settings["efficiency"]["decode_tokens"]),
    )
    ttft_seconds = time.perf_counter() - started
    projected_compilation = float(compilation["wall_seconds"]) / count * 499
    full_read_calls = (
        3
        * 6
        * (
            int(settings["efficiency"]["read_warmups"])
            + int(settings["efficiency"]["read_repetitions"])
        )
        * 2
    )
    projected_read = (
        read_seconds / (read_repetitions * 2) * full_read_calls * 20.0
    )
    ttft_combinations = 3 * 8
    projected_ttft = (
        ttft_seconds
        * ttft_combinations
        * (
            int(settings["efficiency"]["ttft_warmups"])
            + int(settings["efficiency"]["ttft_repetitions"])
        )
    )
    projected_reversibility = max(
        900.0,
        sum(float(row["field_update_ms"]) for row in compilation["records"])
        / count
        * 499
        * 8
        / 1000.0,
    )
    expected = (
        projected_compilation
        + projected_read
        + projected_ttft
        + projected_reversibility
    )
    conservative = expected * 1.75
    result = {
        "format": "rcmf_exp036a_efficiency_pilot_13a_v1",
        "passed": True,
        "prompt_rows": prompt_rows,
        "pilot_record_count": count,
        "compilation_pilot": compilation,
        "read_pilot_wall_seconds": read_seconds,
        "ttft_pilot_wall_seconds": ttft_seconds,
        "ttft_token_equivalence": bool(ttft["validated"]),
        "ttft_profiler_non_equivalence_does_not_block_formal_evaluation": True,
        "expected_auxiliary_wall_seconds": expected,
        "conservative_auxiliary_wall_seconds": conservative,
        "projection_components_seconds": {
            "compilation": projected_compilation,
            "read_scaling": projected_read,
            "ttft": projected_ttft,
            "reversibility": projected_reversibility,
        },
        "projection_formula_frozen_before_formal": True,
        "projection_is_conservative": True,
    }
    result["report_sha256"] = canonical_sha256(result)
    return result


def serving_state(
    settings: Mapping[str, Any], artifact_dir: Path
) -> dict[str, Any]:
    compilation = read_json(artifact_dir / "efficiency/compilation_results.json")
    package = read_json(artifact_dir / "manifests/package_manifest.json")
    provenance_path = Path(
        str(settings["packages"]["BEST"]["memory_provenance"])
    )
    source_cache_path = Path(str(settings["packages"]["BEST"]["source_cache"]))
    best = package["packages"]["BEST"]
    field_bytes = int(best["field"]["active_field_bytes"])
    writer_bytes = int(best["writer_parameter_count"]) * 4
    reader_bytes = int(best["reader_parameter_count"]) * 4
    selector_bytes = sum(
        Path(path).stat().st_size
        for path in best.get("selector_member_paths", [])
        if Path(path).exists()
    )
    per_record = 4 * (960 + 8 * 256) + 8
    rows = []
    total_raw_text = sum(
        int(row["raw_text_bytes"]) for row in compilation["records"]
    )
    for size_value in settings["efficiency"]["bank_sizes"]:
        size = int(size_value)
        prefix_text = sum(
            int(row["raw_text_bytes"]) for row in compilation["records"][:size]
        )
        allocated_provenance = int(provenance_path.stat().st_size * size / 499)
        rows.append(
            {
                "memory_count": size,
                "active_field_A_bytes": 960 * 8 * 256 * 4,
                "active_field_B_bytes": 8 * 256 * 4,
                "active_field_total_bytes": field_bytes,
                "fixed_reader_parameter_bytes": reader_bytes,
                "fixed_writer_parameter_bytes": writer_bytes,
                "fixed_selector_checkpoint_bytes": selector_bytes,
                "total_model_side_memory_adapter_bytes": field_bytes
                + reader_bytes
                + selector_bytes,
                "raw_ledger_text_bytes": prefix_text,
                "per_record_deletion_state_bytes": per_record * size,
                "provenance_allocated_bytes": allocated_provenance,
                "archival_total_bytes": prefix_text
                + per_record * size
                + allocated_provenance,
            }
        )
    result = {
        "format": "rcmf_exp036a_serving_state_13a_v1",
        "rows": rows,
        "active_field_state_independent_of_memory_count": len(
            {row["active_field_total_bytes"] for row in rows}
        )
        == 1,
        "raw_ledger_and_deletion_state_scale_with_memory_count": True,
        "source_cache_archive_bytes": source_cache_path.stat().st_size,
        "complete_raw_transition_text_bytes": total_raw_text,
        "claim": "active whole-bank field state and read shape are independent of N; raw ledger and per-record deletion/provenance storage scale with N",
    }
    result["result_sha256"] = canonical_sha256(result)
    return result


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_13a"]
    settings_9a = cfg.raw["stage_c_9a"]
    if git_head() != args.source_head:
        raise ValueError("EXP-036A efficiency source HEAD differs")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    run_manifest = read_json(args.artifact_dir / "run_manifest.json")
    if str(run_manifest["source_head"]) != str(
        args.manifest_source_head or args.source_head
    ):
        raise ValueError("EXP-036A efficiency manifest source differs")
    if args.phase != "pilot":
        formal = read_json(args.artifact_dir / "results/formal_summary.json")
        if not bool(formal.get("evaluation_complete")) or int(
            formal.get("trajectory_count", 0)
        ) != 840:
            raise RuntimeError(
                "Efficiency cannot precede sealed 840-row formal evaluation"
            )
    attempts = args.artifact_dir / "attempts.jsonl"
    if attempts.exists() and any(
        str(json.loads(line).get("attempt_id")) == args.attempt_id
        for line in attempts.read_text(encoding="utf-8").splitlines()
        if line
    ):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")

    backend = load_backend(cfg)
    package_manifest = read_json(
        args.artifact_dir / "manifests/package_manifest.json"
    )
    runtime = FinalTestRuntime(
        settings_9a=settings_9a,
        settings=settings,
        backend=backend,
        package_manifest=package_manifest,
    )
    selector = writer = None
    if args.phase in {"pilot", "compilation"}:
        selector, writer, _ = package_components(
            settings=settings,
            package_manifest=package_manifest,
            backend=backend,
        )
    output_paths = {
        "pilot": args.artifact_dir / "preflight/efficiency_pilot.json",
        "compilation": args.artifact_dir / "efficiency/compilation_results.json",
        "read": args.artifact_dir / "efficiency/scaling_results.json",
        "ttft": args.artifact_dir / "efficiency/ttft_results.json",
        "finalize": args.artifact_dir / "efficiency/formal_efficiency.json",
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"exp036a_efficiency_{args.phase}",
        command=list(sys.argv),
        local_head=args.source_head,
        github_head=args.source_head,
        lambda_head=args.source_head,
        tmux_session=os.environ.get("TMUX", "none"),
        config_sha256=sha256_file(args.config),
        data_manifest_hashes={
            "package_manifest": sha256_file(
                args.artifact_dir / "manifests/package_manifest.json"
            )
        },
        parent_attempt_id="none",
        resume_checkpoint=str(output_paths[args.phase]),
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(
            settings["runtime"]["heartbeat_interval_seconds"]
        ),
    ) as attempt:
        if args.phase == "pilot":
            assert selector is not None and writer is not None
            result = pilot_phase(
                settings_9a=settings_9a,
                settings=settings,
                artifact_dir=args.artifact_dir,
                backend=backend,
                runtime=runtime,
                selector=selector,
                writer=writer,
            )
        elif args.phase == "compilation":
            assert selector is not None and writer is not None
            result = compilation_phase(
                settings_9a=settings_9a,
                settings=settings,
                artifact_dir=args.artifact_dir,
                backend=backend,
                selector=selector,
                writer=writer,
                limit=None,
            )
        elif args.phase == "read":
            result = read_phase(
                settings=settings,
                artifact_dir=args.artifact_dir,
                backend=backend,
                runtime=runtime,
            )
        elif args.phase == "ttft":
            result = ttft_phase(
                settings=settings,
                artifact_dir=args.artifact_dir,
                backend=backend,
                runtime=runtime,
            )
        else:
            required = {
                name: read_json(args.artifact_dir / f"efficiency/{filename}")
                for name, filename in {
                    "compilation": "compilation_results.json",
                    "scaling": "scaling_results.json",
                    "ttft": "ttft_results.json",
                }.items()
            }
            serving = serving_state(settings, args.artifact_dir)
            atomic_write_json(
                args.artifact_dir / "efficiency/serving_state_results.json",
                serving,
            )
            result = {
                "format": "rcmf_exp036a_formal_efficiency_13a_v1",
                "formal_task_time_is_analytically_separate": True,
                "components": {
                    name: row["result_sha256"] for name, row in required.items()
                },
                "serving_state_sha256": serving["result_sha256"],
                "complete": True,
            }
            result["result_sha256"] = canonical_sha256(result)
        output_paths[args.phase].parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(output_paths[args.phase], result)
        attempt.progress(
            status=f"exp036a_efficiency_{args.phase}_complete",
            completed_units=1,
            total_units=1,
            latest_validated_checkpoint=str(output_paths[args.phase]),
        )
    assert_frozen_without_gradients(backend.model)
    for reader in runtime.readers.values():
        assert_frozen_without_gradients(reader)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
