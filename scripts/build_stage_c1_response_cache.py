from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

import torch

from rcmf.benchmarks.appworld.prompt import appworld_renderer_metadata
from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.schemas import DecisionExample, MemoryRecord
from rcmf.training.datasets import (
    _append_eos_token_id,
    _appworld_messages_from_example,
    _render_prompt_with_metadata,
    _target_suffix,
    load_decision_examples,
    load_memory_records,
)
from rcmf.training.stage_c1 import (
    POSITIVE_TEACHER_EPS,
    STAGE_C1_RESPONSE_CACHE_VERSION,
    STAGE_C1_RESPONSE_SCORING_DEFINITION,
    condition_counts,
    load_teacher_rows,
    select_teacher_conditions,
)
from rcmf.utils.serialization import (
    append_jsonl,
    atomic_write_json,
    atomic_write_text,
    maybe_git_commit,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_jsonl,
)
from scripts.run_raw_text_teacher_pilot import (
    TEACHER_MEMORY_SECTION_VERSION,
    _context_limit_for_backend,
    _target_token_ids,
    _token_ids,
    messages_with_teacher_memory,
)


REPRO_TOLERANCE = 2.0e-4
PROBABILITY_BUCKET_TOLERANCE = 1.0e-5


def utc_now() -> str:
    import datetime as _dt

    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _state_id(index: int, example: DecisionExample) -> str:
    return f"{example.episode_id}:step:{example.step_id}:line:{index + 1}"


def _load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _score_target_logits(
    backend: Any,
    *,
    prompt_text: str,
    target_ids: list[int],
    context_limit: int,
    top_k: int,
) -> dict[str, Any]:
    tokenizer = backend.tokenizer
    prompt_ids = _token_ids(tokenizer, prompt_text, add_special_tokens=False)
    full_ids = prompt_ids + list(target_ids)
    if len(full_ids) > context_limit:
        raise ValueError(f"prompt+target length {len(full_ids)} exceeds context_limit={context_limit}; no truncation is applied")
    input_ids = torch.tensor([full_ids], dtype=torch.long, device=backend.device)
    labels = torch.tensor([[-100] * len(prompt_ids) + list(target_ids)], dtype=torch.long, device=backend.device)
    attention_mask = torch.ones_like(input_ids)
    model_inputs = {"input_ids": input_ids, "attention_mask": attention_mask}
    with torch.no_grad():
        loss, target_logits = backend._target_only_loss_from_hidden(  # noqa: SLF001
            model_inputs=model_inputs,
            labels=labels,
            logit_bias=None,
        )
    logits = target_logits.detach().to(torch.float32).cpu()
    target_tensor = torch.tensor(target_ids, dtype=torch.long)
    logsumexp = torch.logsumexp(logits, dim=-1)
    target_logprobs = logits[torch.arange(logits.shape[0]), target_tensor] - logsumexp
    top_values, top_indices = torch.topk(logits, k=min(top_k, logits.shape[-1]), dim=-1)
    return {
        "mean_target_nll": float(loss.detach().cpu()),
        "prompt_tokens": len(prompt_ids),
        "target_tokens": len(target_ids),
        "target_token_ids": list(target_ids),
        "target_logprobs": [float(item) for item in target_logprobs.tolist()],
        "top_token_ids": [[int(token) for token in row] for row in top_indices.tolist()],
        "top_logits": [[float(value) for value in row] for row in top_values.tolist()],
        "logsumexp": [float(value) for value in logsumexp.tolist()],
        "logits": logits,
    }


def _build_position_rows(
    *,
    baseline: dict[str, Any],
    teacher: dict[str, Any],
    top_k: int,
) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    target_ids = baseline["target_token_ids"]
    if target_ids != teacher["target_token_ids"]:
        raise ValueError("baseline and teacher target token ids differ")
    for pos, target_id in enumerate(target_ids):
        union_ids = sorted(
            set(baseline["top_token_ids"][pos])
            .union(teacher["top_token_ids"][pos])
            .union({int(target_id)})
        )
        union_tensor = torch.tensor(union_ids, dtype=torch.long)
        base_logits = baseline["logits"][pos, union_tensor].to(torch.float64)
        teacher_logits = teacher["logits"][pos, union_tensor].to(torch.float64)
        base_logsumexp = torch.logsumexp(baseline["logits"][pos].to(torch.float64), dim=-1)
        teacher_logsumexp = torch.logsumexp(teacher["logits"][pos].to(torch.float64), dim=-1)
        base_union_logprobs = base_logits - base_logsumexp
        teacher_union_logprobs = teacher_logits - teacher_logsumexp
        base_other = max(0.0, 1.0 - float(base_union_logprobs.exp().sum().item()))
        teacher_other = max(0.0, 1.0 - float(teacher_union_logprobs.exp().sum().item()))
        positions.append(
            {
                "target_position_index": pos,
                "target_token_id": int(target_id),
                "baseline_target_logprob": float(baseline["target_logprobs"][pos]),
                "teacher_target_logprob": float(teacher["target_logprobs"][pos]),
                f"baseline_top{top_k}_token_ids": baseline["top_token_ids"][pos],
                f"baseline_top{top_k}_logits": baseline["top_logits"][pos],
                f"teacher_top{top_k}_token_ids": teacher["top_token_ids"][pos],
                f"teacher_top{top_k}_logits": teacher["top_logits"][pos],
                "union_token_ids": union_ids,
                "baseline_union_logprobs": [float(value) for value in base_union_logprobs.tolist()],
                "teacher_union_logprobs": [float(value) for value in teacher_union_logprobs.tolist()],
                "baseline_logsumexp": float(base_logsumexp.item()),
                "teacher_logsumexp": float(teacher_logsumexp.item()),
                "baseline_other_probability": base_other,
                "teacher_other_probability": teacher_other,
            }
        )
    return positions


def _row_for_state(
    *,
    backend: Any,
    tokenizer: Any,
    condition: dict[str, Any],
    example: DecisionExample,
    record: MemoryRecord | None,
    prompt_profile: str,
    renderer_metadata: dict[str, Any],
    source_commit: str | None,
    model_config_commit_hash: str | None,
    context_limit: int,
    top_k: int,
    corpus_lineage_sha256: str | None = None,
) -> dict[str, Any]:
    base_messages = _appworld_messages_from_example(example, prompt_profile)
    base_prompt, prompt_metadata = _render_prompt_with_metadata(tokenizer, base_messages, prompt_profile)
    target_text = _target_suffix(example)
    target_ids = _target_token_ids(tokenizer, example)
    baseline = _score_target_logits(
        backend,
        prompt_text=base_prompt,
        target_ids=target_ids,
        context_limit=context_limit,
        top_k=top_k,
    )
    teacher_prompt = base_prompt
    memory_tokens = 0
    if condition["condition"] == "positive_teacher":
        if record is None:
            raise ValueError("positive teacher condition requires a memory record")
        teacher_messages = messages_with_teacher_memory(base_messages, record, prompt_profile)
        teacher_prompt = backend.render_messages(teacher_messages, add_generation_prompt=True)
        memory_tokens = len(_token_ids(tokenizer, record.experience_text, add_special_tokens=False))
        teacher = _score_target_logits(
            backend,
            prompt_text=teacher_prompt,
            target_ids=target_ids,
            context_limit=context_limit,
            top_k=top_k,
        )
    else:
        teacher = dict(baseline)
    positions = _build_position_rows(baseline=baseline, teacher=teacher, top_k=top_k)
    row = {
        "format": STAGE_C1_RESPONSE_CACHE_VERSION,
        "scoring_definition": STAGE_C1_RESPONSE_SCORING_DEFINITION,
        "teacher_selection_definition": "argmax_valid_effective_bank_text_utility_else_bare_qwen_gt_0.01_v1",
        "state_index": int(condition["state_index"]),
        "state_example_id": condition["state_example_id"],
        "task_id": condition["task_id"],
        "episode_id": condition["episode_id"],
        "step_id": condition["step_id"],
        "split": condition["split"],
        "teacher_condition": condition["condition"],
        "valid_for_stage_c": bool(condition["valid_for_stage_c"]),
        "all_missing_state": bool(condition["all_missing_state"]),
        "no_positive_state": bool(condition["no_positive_state"]),
        "best_memory_id": condition["best_memory_id"],
        "best_memory_index": condition["best_memory_index"],
        "best_pair_key": condition["best_pair_key"],
        "L0": condition["L0"],
        "teacher_Lj_text": condition["Lj_text"],
        "teacher_utility": condition["best_utility"],
        "baseline_mean_target_nll": baseline["mean_target_nll"],
        "teacher_mean_target_nll": teacher["mean_target_nll"],
        "prompt_tokens": baseline["prompt_tokens"],
        "teacher_prompt_tokens": teacher["prompt_tokens"],
        "raw_memory_tokens": memory_tokens,
        "target_tokens": baseline["target_tokens"],
        "total_tokens_with_target": baseline["prompt_tokens"] + baseline["target_tokens"],
        "teacher_total_tokens_with_target": teacher["prompt_tokens"] + teacher["target_tokens"],
        "context_limit": context_limit,
        "truncated": False,
        "target_sha256": sha256_text(target_text),
        "target_token_sha256": sha256_text(",".join(str(item) for item in target_ids)),
        "prompt_sha256": sha256_text(base_prompt),
        "teacher_prompt_sha256": sha256_text(teacher_prompt),
        "memory_text_sha256": None if record is None else sha256_text(record.experience_text),
        "target_token_ids": target_ids,
        "target_positions": positions,
        "last_user_token_indices": prompt_metadata.get("last_user_token_indices", []),
        "renderer_version": renderer_metadata["renderer_version"],
        "renderer_metadata": renderer_metadata,
        "teacher_memory_section_version": TEACHER_MEMORY_SECTION_VERSION,
        "top_k": top_k,
        "model_name": backend.model_name,
        "checkpoint_identity": f"frozen_hf_pretrained:{backend.model_name}",
        "model_config_commit_hash": model_config_commit_hash,
        "source_commit_sha": source_commit,
        "scoring_timestamp_utc": utc_now(),
    }
    if corpus_lineage_sha256 is not None:
        row["corpus_lineage_sha256"] = corpus_lineage_sha256
    del baseline["logits"], teacher["logits"]
    return row


def validate_response_cache(
    rows: list[dict[str, Any]],
    *,
    label_rows: list[dict[str, Any]],
    memory_bank: list[dict[str, Any]],
    teacher_rows: dict[str, dict[str, Any]],
    tolerance: float = REPRO_TOLERANCE,
    bucket_tolerance: float = PROBABILITY_BUCKET_TOLERANCE,
) -> dict[str, Any]:
    errors: list[str] = []
    by_state: dict[str, dict[str, Any]] = {}
    expected_states = {str(row["state_example_id"]) for row in label_rows}
    for row in rows:
        state_id = str(row.get("state_example_id"))
        if state_id in by_state:
            errors.append(f"duplicate_state:{state_id}")
        by_state[state_id] = row
        if row.get("format") != STAGE_C1_RESPONSE_CACHE_VERSION:
            errors.append(f"{state_id}:bad_format:{row.get('format')}")
        if row.get("scoring_definition") != STAGE_C1_RESPONSE_SCORING_DEFINITION:
            errors.append(f"{state_id}:bad_scoring_definition:{row.get('scoring_definition')}")
        if row.get("truncated"):
            errors.append(f"{state_id}:truncated")
        if int(row.get("teacher_total_tokens_with_target", 0)) > int(row.get("context_limit", 0)):
            errors.append(f"{state_id}:teacher_over_context_without_mask")
        target_hash = row.get("target_token_sha256")
        if target_hash != sha256_text(",".join(str(item) for item in row.get("target_token_ids", []))):
            errors.append(f"{state_id}:target_token_hash_mismatch")
        positions = row.get("target_positions", [])
        if len(positions) != int(row.get("target_tokens", -1)):
            errors.append(f"{state_id}:position_count_mismatch")
        if positions:
            base_nll = -sum(float(item["baseline_target_logprob"]) for item in positions) / len(positions)
            teacher_nll = -sum(float(item["teacher_target_logprob"]) for item in positions) / len(positions)
            if abs(base_nll - float(row["baseline_mean_target_nll"])) > tolerance:
                errors.append(f"{state_id}:baseline_position_nll_mismatch:{base_nll}:{row['baseline_mean_target_nll']}")
            if abs(teacher_nll - float(row["teacher_mean_target_nll"])) > tolerance:
                errors.append(f"{state_id}:teacher_position_nll_mismatch:{teacher_nll}:{row['teacher_mean_target_nll']}")
            if row.get("L0") is not None and abs(base_nll - float(row["L0"])) > tolerance:
                errors.append(f"{state_id}:L0_reproduction_mismatch:{base_nll}:{row['L0']}")
            if row.get("teacher_condition") == "positive_teacher":
                if row.get("teacher_Lj_text") is None or abs(teacher_nll - float(row["teacher_Lj_text"])) > tolerance:
                    errors.append(f"{state_id}:Lj_reproduction_mismatch:{teacher_nll}:{row.get('teacher_Lj_text')}")
            else:
                if abs(teacher_nll - base_nll) > tolerance:
                    errors.append(f"{state_id}:baseline_teacher_not_equal_bare")
        for pos, item in enumerate(positions):
            for prefix in ("baseline", "teacher"):
                probs = torch.tensor(item[f"{prefix}_union_logprobs"], dtype=torch.float64).exp()
                total = float(probs.sum().item()) + float(item[f"{prefix}_other_probability"])
                if abs(total - 1.0) > bucket_tolerance:
                    errors.append(f"{state_id}:probability_bucket_sum:{prefix}:{pos}:{total}")
        if row.get("teacher_condition") == "positive_teacher":
            pair = teacher_rows.get(str(row.get("best_pair_key")))
            if pair is None:
                errors.append(f"{state_id}:missing_selected_teacher_pair")
            else:
                if pair.get("leakage_overlap"):
                    errors.append(f"{state_id}:selected_pair_leakage:{pair.get('leakage_overlap')}")
                if str(pair.get("candidate_memory_id")) != str(row.get("best_memory_id")):
                    errors.append(f"{state_id}:selected_memory_id_mismatch")
                if str(pair.get("memory_text_sha256")) != str(row.get("memory_text_sha256")):
                    errors.append(f"{state_id}:selected_memory_hash_mismatch")
    missing_states = sorted(expected_states.difference(by_state))
    unexpected_states = sorted(set(by_state).difference(expected_states))
    if missing_states:
        errors.append(f"missing_states:{missing_states[:10]}")
    if unexpected_states:
        errors.append(f"unexpected_states:{unexpected_states[:10]}")
    memory_task_ids = {str(row["task_id"]) for row in memory_bank}
    validation_task_ids = {str(row["task_id"]) for row in label_rows if str(row.get("split")) == "validation"}
    leaked_validation_memories = sorted(memory_task_ids.intersection(validation_task_ids))
    if leaked_validation_memories:
        errors.append(f"validation_task_memories_visible:{leaked_validation_memories[:10]}")
    return {
        "format": "stage_c1_response_cache_validation_v1",
        "passed": not errors,
        "error_count": len(errors),
        "errors_first_50": errors[:50],
        "state_count": len(rows),
        "expected_state_count": len(expected_states),
        "target_nll_tolerance": tolerance,
        "probability_bucket_tolerance": bucket_tolerance,
    }


def _report(summary: dict[str, Any]) -> str:
    validation = summary["validation"]
    counts = summary["condition_counts"]
    lines = [
        "# Stage C1 Teacher-Response Cache",
        "",
        f"- format: `{summary['format']}`",
        f"- scoring definition: `{summary['scoring_definition']}`",
        f"- artifact: `{summary['output_dir']}`",
        f"- source commit: `{summary['source_commit']}`",
        f"- validation passed: `{validation['passed']}`",
        f"- states: `{counts['states']}`",
        f"- positive-teacher: `{counts['positive_teacher']}`",
        f"- baseline-teacher/no-positive: `{counts['baseline_teacher']}`",
        f"- all-missing: `{counts['all_missing']}`",
        f"- runtime seconds: `{summary['runtime_s']:.2f}`",
        f"- cache size bytes: `{summary['cache_size_bytes']}`",
        "",
        "## Teacher Improvement",
        "",
        f"```json\n{json.dumps(summary['cache_metrics']['teacher_improvement'], indent=2, sort_keys=True)}\n```",
        "",
        "## Validation",
        "",
        f"```json\n{json.dumps(validation, indent=2, sort_keys=True)}\n```",
        "",
        "## Representative Rows",
        "",
    ]
    for name, row in summary["representative_rows"].items():
        if row is None:
            lines.append(f"- {name}: none")
            continue
        lines.append(
            f"- {name}: state `{row['state_example_id']}`, memory `{row.get('best_memory_id')}`, "
            f"condition `{row['teacher_condition']}`, improvement `{float(row['L0']) - float(row['teacher_mean_target_nll']):.6f}`"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Stage-C1 best-raw-memory teacher-response cache.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--teacher-cache-dir", required=True)
    parser.add_argument("--labels-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--top-k", type=int, default=64)
    parser.add_argument("--progress-interval-s", type=float, default=120.0)
    parser.add_argument("--corpus-lineage-sha256", default=None)
    args = parser.parse_args()

    started = time.perf_counter()
    cfg = load_config(args.config)
    if cfg.model.backend != "hf_qwen":
        raise ValueError("Stage-C1 response cache requires hf_qwen")
    backend = build_backend(cfg, load_model=True)
    tokenizer = backend.tokenizer
    context_limit = _context_limit_for_backend(backend)
    data_dir = Path(args.data)
    teacher_dir = Path(args.teacher_cache_dir)
    labels_dir = Path(args.labels_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    source_commit = maybe_git_commit()

    examples = load_decision_examples(data_dir / "decision_examples.jsonl")
    records = load_memory_records(data_dir / "memory_records.jsonl")
    label_rows = _load_jsonl_rows(labels_dir / "student_labels.jsonl")
    memory_bank = _load_jsonl_rows(labels_dir / "effective_memory_bank.jsonl")
    teacher_rows = load_teacher_rows(read_jsonl(teacher_dir / "teacher_cache_full_rows.jsonl"))
    conditions = select_teacher_conditions(label_rows, memory_bank, teacher_rows, positive_eps=POSITIVE_TEACHER_EPS)
    atomic_write_json(output_dir / "teacher_conditions.json", {"format": "stage_c1_teacher_conditions_v1", "rows": conditions})

    rows_path = output_dir / "response_cache.jsonl"
    completed = {
        str(row["state_example_id"]): row
        for row in read_jsonl(rows_path)
        if row.get("format") == STAGE_C1_RESPONSE_CACHE_VERSION
    }
    renderer_metadata = appworld_renderer_metadata(cfg.benchmark.prompt_profile, add_generation_prompt=True)
    model_hash = getattr(getattr(backend.model, "config", None), "_commit_hash", None)
    last_progress = 0.0
    for index, condition in enumerate(conditions):
        state_id = str(condition["state_example_id"])
        if state_id in completed:
            continue
        example = examples[int(condition["state_index"])]
        record = None
        if condition["best_memory_index"] is not None:
            record = records[int(condition["best_memory_index"])]
        row = _row_for_state(
            backend=backend,
            tokenizer=tokenizer,
            condition=condition,
            example=example,
            record=record if condition["condition"] == "positive_teacher" else None,
            prompt_profile=cfg.benchmark.prompt_profile,
            renderer_metadata=renderer_metadata,
            source_commit=source_commit,
            model_config_commit_hash=model_hash,
            context_limit=context_limit,
            top_k=args.top_k,
            corpus_lineage_sha256=args.corpus_lineage_sha256,
        )
        completed[state_id] = row
        append_jsonl(rows_path, row)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        now = time.perf_counter()
        if now - last_progress >= args.progress_interval_s:
            last_progress = now
            print(
                f"response-cache progress {len(completed)}/{len(conditions)} "
                f"elapsed={(now - started) / 3600.0:.2f}h current={state_id}",
                flush=True,
            )
            atomic_write_json(
                output_dir / "progress.json",
                {"completed": len(completed), "total": len(conditions), "elapsed_s": now - started, "current_state": state_id},
            )

    rows = [completed[str(condition["state_example_id"])] for condition in conditions]
    write_jsonl(rows_path, rows)
    validation = validate_response_cache(
        rows,
        label_rows=label_rows,
        memory_bank=memory_bank,
        teacher_rows=teacher_rows,
    )
    atomic_write_json(output_dir / "validation.json", validation)
    metrics = __import__("rcmf.training.stage_c1", fromlist=["response_cache_state_metrics"]).response_cache_state_metrics(rows)
    improvements = [
        (float(row["L0"]) - float(row["teacher_mean_target_nll"]), row)
        for row in rows
        if row.get("valid_for_stage_c")
    ]
    positives = [(value, row) for value, row in improvements if value > 0.01]
    neutrals = [(value, row) for value, row in improvements if abs(value) <= 0.01]
    negatives = [(value, row) for value, row in improvements if value < -0.01]
    summary = {
        "format": STAGE_C1_RESPONSE_CACHE_VERSION,
        "scoring_definition": STAGE_C1_RESPONSE_SCORING_DEFINITION,
        "teacher_memory_section_version": TEACHER_MEMORY_SECTION_VERSION,
        "source_commit": source_commit,
        "output_dir": str(output_dir),
        "data_dir": str(data_dir),
        "teacher_cache_dir": str(teacher_dir),
        "labels_dir": str(labels_dir),
        "model_name": backend.model_name,
        "checkpoint_identity": f"frozen_hf_pretrained:{backend.model_name}",
        "model_config_commit_hash": model_hash,
        "context_limit": context_limit,
        "top_k": args.top_k,
        "condition_counts": condition_counts(conditions),
        "cache_metrics": metrics,
        "runtime_s": time.perf_counter() - started,
        "cache_size_bytes": rows_path.stat().st_size if rows_path.exists() else 0,
        "response_cache_sha256": sha256_file(rows_path),
        "validation": validation,
        "corpus_lineage_sha256": args.corpus_lineage_sha256,
        "representative_rows": {
            "positive": max(positives, key=lambda item: item[0])[1] if positives else None,
            "neutral": min(neutrals, key=lambda item: abs(item[0]))[1] if neutrals else None,
            "negative": min(negatives, key=lambda item: item[0])[1] if negatives else None,
        },
    }
    atomic_write_json(output_dir / "summary.json", summary)
    atomic_write_text(output_dir / "report.md", _report(summary))
    if not validation["passed"]:
        raise SystemExit(f"Stage-C1 response cache validation failed: {validation['errors_first_50']}")
    print(f"Wrote Stage-C1 response cache to {output_dir}", flush=True)


if __name__ == "__main__":
    main()
