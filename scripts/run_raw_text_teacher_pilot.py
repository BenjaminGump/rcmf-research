from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import math
import random
import re
import statistics
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
    _target_suffix,
    load_decision_examples,
    load_memory_records,
)
from rcmf.utils.serialization import (
    append_jsonl,
    atomic_write_json,
    maybe_git_commit,
    sha256_file,
    sha256_text,
)
from scripts.train import (
    _aggregate_chunk_representations,
    _example_task_id,
    _leakage_keys_for_example,
    _leakage_keys_for_record,
)


RAW_TEXT_TEACHER_CACHE_VERSION = "raw_text_memory_teacher_labels_v1"
TEACHER_MEMORY_SECTION_VERSION = "teacher_only_raw_memory_section_v1"
UTILITY_NEUTRAL_EPS = 0.01


APP_RE = re.compile(r"\bapis\.([A-Za-z_][A-Za-z0-9_]*)")
URL_APP_RE = re.compile(r"['\"]/(?:api/)?([A-Za-z_][A-Za-z0-9_-]*)(?:/|['\"])" )


def _token_ids(tokenizer: Any, text: str, add_special_tokens: bool = False) -> list[int]:
    encoded = tokenizer(
        text,
        truncation=False,
        add_special_tokens=add_special_tokens,
    )["input_ids"]
    if hasattr(encoded, "tolist"):
        encoded = encoded.tolist()
    if encoded and isinstance(encoded[0], list):
        encoded = encoded[0]
    return [int(item) for item in encoded]


def _target_token_ids(tokenizer: Any, example: DecisionExample) -> list[int]:
    return _append_eos_token_id(
        tokenizer,
        _token_ids(tokenizer, _target_suffix(example), add_special_tokens=False),
    )


def _example_id(index: int, example: DecisionExample) -> str:
    return f"{example.episode_id}:step:{example.step_id}:line:{index + 1}"


def _value_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        text = value.strip()
        return {text} if text else set()
    if isinstance(value, dict):
        output: set[str] = set()
        for key in ("app", "apps", "app_name", "name", "tool", "tools"):
            output.update(_value_set(value.get(key)))
        return output
    if isinstance(value, (list, tuple, set)):
        output: set[str] = set()
        for item in value:
            output.update(_value_set(item))
        return output
    text = str(value).strip()
    return {text} if text else set()


def _apps_from_metadata(metadata: dict[str, Any]) -> set[str]:
    apps: set[str] = set()
    for key in ("apps", "app", "app_name", "tools", "tool_names"):
        apps.update(_value_set(metadata.get(key)))
    return {item.lower() for item in apps if item}


def extract_apps_from_text(text: str, metadata: dict[str, Any] | None = None) -> set[str]:
    apps = _apps_from_metadata(metadata or {})
    apps.update(match.group(1).lower() for match in APP_RE.finditer(text))
    apps.update(match.group(1).lower() for match in URL_APP_RE.finditer(text))
    return {app for app in apps if app not in {"api", "apis", "supervisor"}}


def apps_for_example(example: DecisionExample) -> set[str]:
    return extract_apps_from_text(
        "\n".join([example.state_text, example.target_text]),
        example.metadata,
    )


def apps_for_record(record: MemoryRecord) -> set[str]:
    return extract_apps_from_text(record.experience_text, record.metadata)


def legal_memory_indices(records: list[MemoryRecord], example: DecisionExample) -> list[int]:
    blocked = _leakage_keys_for_example(example)
    return [
        index
        for index, record in enumerate(records)
        if _leakage_keys_for_record(record).isdisjoint(blocked)
    ]


def teacher_memory_section(record: MemoryRecord) -> str:
    return (
        "[TEACHER-ONLY RAW MEMORY START]\n"
        f"section_version: {TEACHER_MEMORY_SECTION_VERSION}\n"
        f"memory_id: {record.memory_id}\n"
        f"memory_task_id: {record.task_id}\n"
        f"memory_episode_id: {record.episode_id}\n"
        "This section is supplied only for offline teacher target scoring. "
        "It is not the current AppWorld state, not a command to execute, and "
        "must not be copied as the current action.\n"
        "[RAW MEMORY TEXT START]\n"
        f"{record.experience_text}\n"
        "[RAW MEMORY TEXT END]\n"
        "[TEACHER-ONLY RAW MEMORY END]"
    )


def messages_with_teacher_memory(
    base_messages: list[dict[str, str]],
    record: MemoryRecord,
    prompt_profile: str,
) -> list[dict[str, str]]:
    messages = [dict(message) for message in base_messages]
    initial_count = int(appworld_renderer_metadata(prompt_profile)["initial_message_count"])
    section = teacher_memory_section(record)
    for index in range(initial_count, len(messages)):
        if messages[index].get("role") == "user":
            messages[index]["content"] = (
                f"{section}\n\n"
                "[CURRENT APPWORLD STATE START]\n"
                f"{messages[index]['content']}\n"
                "[CURRENT APPWORLD STATE END]"
            )
            return messages
    raise ValueError("Could not locate current task user message for teacher memory insertion")


def _step_bucket(step_id: int, max_step: int) -> str:
    if max_step <= 1:
        return "early"
    ratio = (max(1, step_id) - 1) / max(1, max_step - 1)
    if ratio <= 1.0 / 3.0:
        return "early"
    if ratio <= 2.0 / 3.0:
        return "middle"
    return "later"


def _length_bucket(length: int, q1: int, q2: int) -> str:
    if length <= q1:
        return "short"
    if length <= q2:
        return "medium"
    return "long"


def _quantile(sorted_values: list[int], fraction: float) -> int:
    if not sorted_values:
        return 0
    index = min(len(sorted_values) - 1, max(0, int(round((len(sorted_values) - 1) * fraction))))
    return int(sorted_values[index])


def select_pilot_examples(
    examples: list[DecisionExample],
    prompt_token_counts: list[int],
    pilot_size: int,
) -> list[int]:
    lengths = sorted(prompt_token_counts)
    q1 = _quantile(lengths, 1.0 / 3.0)
    q2 = _quantile(lengths, 2.0 / 3.0)
    max_step_by_episode: dict[str, int] = defaultdict(int)
    for example in examples:
        max_step_by_episode[example.episode_id] = max(max_step_by_episode[example.episode_id], example.step_id)

    rows: list[dict[str, Any]] = []
    for index, example in enumerate(examples):
        apps = sorted(apps_for_example(example)) or ["unknown"]
        rows.append(
            {
                "index": index,
                "task_id": _example_task_id(example),
                "episode_id": example.episode_id,
                "apps_key": ",".join(apps[:3]),
                "step_bucket": _step_bucket(example.step_id, max_step_by_episode[example.episode_id]),
                "length_bucket": _length_bucket(prompt_token_counts[index], q1, q2),
                "prompt_tokens": prompt_token_counts[index],
                "step_id": example.step_id,
            }
        )
    cell_order = [
        (step_bucket, length_bucket)
        for step_bucket in ("early", "middle", "later")
        for length_bucket in ("short", "medium", "long")
    ]
    by_cell: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_cell[(row["step_bucket"], row["length_bucket"])].append(row)
    for cell_rows in by_cell.values():
        cell_rows.sort(
            key=lambda row: (
                row["apps_key"],
                row["task_id"],
                row["prompt_tokens"],
                row["step_id"],
                row["index"],
            )
        )

    selected: list[int] = []
    selected_set: set[int] = set()
    seen_tasks: set[str] = set()
    seen_apps: set[str] = set()

    def choose(avoid_task: bool, avoid_app: bool) -> None:
        nonlocal selected
        made_progress = True
        while len(selected) < pilot_size and made_progress:
            made_progress = False
            for cell in cell_order:
                if len(selected) >= pilot_size:
                    break
                for row in by_cell.get(cell, []):
                    if row["index"] in selected_set:
                        continue
                    if avoid_task and row["task_id"] in seen_tasks:
                        continue
                    if avoid_app and row["apps_key"] in seen_apps:
                        continue
                    selected.append(row["index"])
                    selected_set.add(row["index"])
                    seen_tasks.add(row["task_id"])
                    seen_apps.add(row["apps_key"])
                    made_progress = True
                    break

    choose(avoid_task=True, avoid_app=True)
    choose(avoid_task=True, avoid_app=False)
    choose(avoid_task=False, avoid_app=False)
    if len(selected) < min(pilot_size, len(examples)):
        raise ValueError(f"Could only select {len(selected)} pilot examples")
    return selected[:pilot_size]


def _load_or_compute_representations(
    backend: Any,
    examples: list[DecisionExample],
    records: list[MemoryRecord],
    selected_indices: list[int],
    prompt_profile: str,
    output_dir: Path,
    batch_size: int,
) -> tuple[torch.Tensor, torch.Tensor, dict[str, Any]]:
    cache_path = output_dir / "representation_cache.pt"
    source_signature = {
        "example_ids": [_example_id(index, examples[index]) for index in selected_indices],
        "memory_ids": [record.memory_id for record in records],
        "model_name": backend.model_name,
        "prompt_profile": prompt_profile,
        "format": "raw_text_teacher_representation_cache_v1",
    }
    if cache_path.exists():
        payload = torch.load(cache_path, map_location="cpu")
        if payload.get("source_signature") == source_signature:
            return (
                payload["state_representations"].to(torch.float32),
                payload["memory_representations"].to(torch.float32),
                dict(payload.get("chunk_audit") or {}),
            )

    state_texts = [
        backend.render_messages(
            _appworld_messages_from_example(examples[index], prompt_profile),
            add_generation_prompt=True,
        )
        for index in selected_indices
    ]
    state_representations = backend.encode_texts(
        state_texts,
        batch_size=batch_size,
        add_special_tokens=True,
    ).to(torch.float32)
    chunks, owners, token_counts = backend.encode_text_chunks_with_metadata(
        [record.experience_text for record in records],
        batch_size=batch_size,
        add_special_tokens=True,
    )
    memory_representations, chunk_audit = _aggregate_chunk_representations(
        chunks.to(torch.float32).cpu(),
        owners.to(torch.long).cpu(),
        token_counts.to(torch.long).cpu(),
        records,
    )
    torch.save(
        {
            "source_signature": source_signature,
            "state_representations": state_representations.cpu(),
            "memory_representations": memory_representations.cpu(),
            "chunk_audit": chunk_audit,
            "created_at": time.time(),
        },
        cache_path,
    )
    return state_representations.cpu(), memory_representations.cpu(), chunk_audit


def propose_candidates(
    example: DecisionExample,
    state_representation: torch.Tensor,
    memory_representations: torch.Tensor,
    records: list[MemoryRecord],
    record_apps: list[set[str]],
    example_apps: set[str],
    seed: int,
) -> dict[int, list[str]]:
    legal = legal_memory_indices(records, example)
    if not legal:
        raise ValueError(f"No legal teacher memories for example {example.episode_id} step {example.step_id}")
    state = torch.nn.functional.normalize(state_representation.to(torch.float32), dim=0)
    memory = torch.nn.functional.normalize(memory_representations[legal].to(torch.float32), dim=-1)
    similarities = (memory @ state).tolist()
    ranked = sorted(zip(legal, similarities), key=lambda item: (-item[1], records[item[0]].memory_id))
    candidates: dict[int, list[str]] = {}

    def add(index: int, source: str) -> None:
        candidates.setdefault(index, [])
        if source not in candidates[index]:
            candidates[index].append(source)

    for index, _score in ranked[:2]:
        add(index, "cosine_top2")

    if example_apps:
        same_app = [
            index
            for index, _score in ranked
            if example_apps.intersection(record_apps[index])
        ]
        for index in same_app[:2]:
            add(index, "same_app")

    low_pool = [index for index, _score in sorted(zip(legal, similarities), key=lambda item: (item[1], records[item[0]].memory_id))]
    half = max(1, len(low_pool) // 2)
    low_pool = low_pool[:half]
    rng = random.Random(seed)
    low_choices = list(low_pool)
    rng.shuffle(low_choices)
    for index in low_choices[:2]:
        add(index, "random_low_similarity")
    return candidates


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    denom = math.sqrt(sum(value * value for value in dx) * sum(value * value for value in dy))
    if denom <= 0:
        return None
    return float(sum(x * y for x, y in zip(dx, dy)) / denom)


def _distribution(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {
            "count": 0,
            "mean": None,
            "std": None,
            "min": None,
            "p05": None,
            "p25": None,
            "p50": None,
            "p75": None,
            "p95": None,
            "max": None,
        }
    sorted_values = sorted(values)

    def percentile(fraction: float) -> float:
        if len(sorted_values) == 1:
            return float(sorted_values[0])
        position = (len(sorted_values) - 1) * fraction
        lower = int(math.floor(position))
        upper = int(math.ceil(position))
        if lower == upper:
            return float(sorted_values[lower])
        weight = position - lower
        return float(sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight)

    return {
        "count": len(values),
        "mean": float(statistics.fmean(values)),
        "std": float(statistics.pstdev(values)) if len(values) > 1 else 0.0,
        "min": float(sorted_values[0]),
        "p05": percentile(0.05),
        "p25": percentile(0.25),
        "p50": percentile(0.50),
        "p75": percentile(0.75),
        "p95": percentile(0.95),
        "max": float(sorted_values[-1]),
    }


def _category(utility: float) -> str:
    if utility > UTILITY_NEUTRAL_EPS:
        return "positive"
    if utility < -UTILITY_NEUTRAL_EPS:
        return "negative"
    return "neutral"


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Primary Raw-Text Memory Teacher Pilot",
        "",
        f"- version: `{summary['teacher_cache_version']}`",
        f"- commit: `{summary['commit_sha']}`",
        f"- model: `{summary['model_name']}`",
        f"- selected states: {summary['selected_state_count']}",
        f"- candidate pairs proposed: {summary['candidate_pair_count']}",
        f"- scored rows: {summary['scored_row_count']}",
        f"- skipped over-context rows: {summary['over_context_pair_count']}",
        f"- runtime seconds: {summary['runtime_s']:.2f}",
        "",
        "## Utility",
        "",
        f"- positive: {summary['utility_counts'].get('positive', 0)}",
        f"- neutral: {summary['utility_counts'].get('neutral', 0)}",
        f"- negative: {summary['utility_counts'].get('negative', 0)}",
        f"- mean: {summary['utility_distribution']['mean']}",
        f"- std: {summary['utility_distribution']['std']}",
        f"- min: {summary['utility_distribution']['min']}",
        f"- p50: {summary['utility_distribution']['p50']}",
        f"- max: {summary['utility_distribution']['max']}",
        "",
        "## Correlations",
        "",
        f"- utility vs raw memory tokens: {summary['correlations']['utility_vs_memory_tokens']}",
        f"- utility vs combined context tokens: {summary['correlations']['utility_vs_combined_context_tokens']}",
        "",
        "## Audit Recall",
        "",
        f"- audited states: {summary['audit']['audited_state_count']}",
        f"- recall@proposed: {summary['audit']['candidate_recall_at_proposed']}",
        "",
        "## Representative Rows",
        "",
    ]
    for name, row in summary["representative_rows"].items():
        if row is None:
            lines.append(f"- {name}: none")
            continue
        lines.append(
            f"- {name}: state={row['state_example_id']} memory={row['candidate_memory_id']} "
            f"utility={row['text_utility']:.6f} source={','.join(row['candidate_source'])}"
        )
    lines.extend(["", "## Over-Context Pairs", ""])
    for row in summary["over_context_pairs"][:20]:
        lines.append(
            f"- state={row['state_example_id']} memory={row['candidate_memory_id']} "
            f"total={row['total_tokens_with_target']}"
        )
    if summary["over_context_pair_count"] > 20:
        lines.append(f"- ... {summary['over_context_pair_count'] - 20} additional over-context pairs")
    return "\n".join(lines) + "\n"


def _score_mean_target_nll(
    backend: Any,
    prompt_text: str,
    target_ids: list[int],
    target_text: str,
    context_limit: int,
) -> tuple[float, int, int]:
    tokenizer = backend.tokenizer
    prompt_ids = _token_ids(tokenizer, prompt_text, add_special_tokens=False)
    input_ids_list = prompt_ids + list(target_ids)
    if len(input_ids_list) > context_limit:
        raise ValueError(
            f"prompt+target length {len(input_ids_list)} exceeds context limit {context_limit}"
        )
    input_ids = torch.tensor([input_ids_list], dtype=torch.long, device=backend.device)
    labels = torch.tensor(
        [[-100] * len(prompt_ids) + list(target_ids)],
        dtype=torch.long,
        device=backend.device,
    )
    attention_mask = torch.ones_like(input_ids)
    with torch.no_grad():
        output = backend.forward_train(
            input_ids=input_ids,
            attention_mask=attention_mask,
            labels=labels,
            injector=None,
            memory_z=None,
        )
    if output.loss is None:
        raise RuntimeError("Frozen Qwen scoring did not return a target loss")
    del target_text
    return float(output.loss.detach().cpu()), len(prompt_ids), len(target_ids)


def _context_limit_for_backend(backend: Any) -> int:
    model_limit = getattr(getattr(backend.model, "config", None), "max_position_embeddings", None)
    if model_limit is not None:
        return int(model_limit)
    tokenizer_limit = getattr(backend.tokenizer, "model_max_length", None)
    if tokenizer_limit is not None and int(tokenizer_limit) < 1_000_000_000:
        return int(tokenizer_limit)
    return 32768


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a primary raw-text memory teacher-label pilot.")
    parser.add_argument("--config", default="configs/benchmark/appworld_rcmf_full_prompt.yaml")
    parser.add_argument("--data", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--pilot-size", type=int, default=24)
    parser.add_argument("--audit-size", type=int, default=4)
    parser.add_argument("--representation-batch-size", type=int, default=1)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-score-pairs", type=int, default=None)
    args = parser.parse_args()

    started = time.perf_counter()
    cfg = load_config(args.config)
    if cfg.model.backend != "hf_qwen":
        raise ValueError("Primary raw-text teacher pilot requires hf_qwen backend")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = Path(args.data)
    examples = load_decision_examples(data_dir / "decision_examples.jsonl")
    records = load_memory_records(data_dir / "memory_records.jsonl")
    backend = build_backend(cfg, load_model=True)
    context_limit = _context_limit_for_backend(backend)
    tokenizer = backend.tokenizer
    prompt_profile = cfg.benchmark.prompt_profile
    renderer_metadata = appworld_renderer_metadata(prompt_profile, add_generation_prompt=True)
    commit_sha = maybe_git_commit() or "unknown"

    base_messages_by_index = {
        index: _appworld_messages_from_example(example, prompt_profile)
        for index, example in enumerate(examples)
    }
    base_prompt_texts = {
        index: backend.render_messages(messages, add_generation_prompt=True)
        for index, messages in base_messages_by_index.items()
    }
    prompt_token_counts = [
        len(_token_ids(tokenizer, base_prompt_texts[index], add_special_tokens=False))
        for index in range(len(examples))
    ]
    selected_indices = select_pilot_examples(
        examples,
        prompt_token_counts,
        pilot_size=min(args.pilot_size, len(examples)),
    )
    selected_set = set(selected_indices)
    audit_indices = selected_indices[: min(args.audit_size, len(selected_indices))]
    state_representations, memory_representations, chunk_audit = _load_or_compute_representations(
        backend=backend,
        examples=examples,
        records=records,
        selected_indices=selected_indices,
        prompt_profile=prompt_profile,
        output_dir=output_dir,
        batch_size=args.representation_batch_size,
    )
    selected_position = {example_index: offset for offset, example_index in enumerate(selected_indices)}
    record_apps = [apps_for_record(record) for record in records]

    pilot_state_rows = []
    pair_sources: dict[tuple[int, int], list[str]] = {}
    proposed_pair_count = 0
    for example_index in selected_indices:
        example = examples[example_index]
        example_apps = apps_for_example(example)
        candidate_sources = propose_candidates(
            example=example,
            state_representation=state_representations[selected_position[example_index]],
            memory_representations=memory_representations,
            records=records,
            record_apps=record_apps,
            example_apps=example_apps,
            seed=args.seed * 1000003 + example_index,
        )
        proposed_pair_count += len(candidate_sources)
        for memory_index, sources in candidate_sources.items():
            pair_sources[(example_index, memory_index)] = list(sources)
        pilot_state_rows.append(
            {
                "example_index": example_index,
                "jsonl_line": example_index + 1,
                "state_example_id": _example_id(example_index, example),
                "task_id": _example_task_id(example),
                "episode_id": example.episode_id,
                "step_id": example.step_id,
                "apps": sorted(example_apps),
                "prompt_tokens": prompt_token_counts[example_index],
                "candidate_memory_ids": [
                    records[index].memory_id for index in sorted(candidate_sources)
                ],
                "audit_all_legal_memories": example_index in set(audit_indices),
            }
        )

    for example_index in audit_indices:
        for memory_index in legal_memory_indices(records, examples[example_index]):
            pair_sources.setdefault((example_index, memory_index), [])
            if "audit_all_memory" not in pair_sources[(example_index, memory_index)]:
                pair_sources[(example_index, memory_index)].append("audit_all_memory")

    atomic_write_json(output_dir / "pilot_states.json", pilot_state_rows)

    target_ids_by_index = {
        index: _target_token_ids(tokenizer, examples[index])
        for index in selected_indices
    }
    target_text_by_index = {
        index: _target_suffix(examples[index])
        for index in selected_indices
    }
    l0_by_index: dict[int, float] = {}
    l0_prompt_tokens: dict[int, int] = {}
    target_token_counts: dict[int, int] = {}
    for example_index in selected_indices:
        loss, prompt_tokens, target_tokens = _score_mean_target_nll(
            backend,
            base_prompt_texts[example_index],
            target_ids_by_index[example_index],
            target_text_by_index[example_index],
            context_limit,
        )
        l0_by_index[example_index] = loss
        l0_prompt_tokens[example_index] = prompt_tokens
        target_token_counts[example_index] = target_tokens
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    preflight_rows: list[dict[str, Any]] = []
    rendered_memory_prompts: dict[tuple[int, int], str] = {}
    for example_index, memory_index in sorted(pair_sources):
        example = examples[example_index]
        record = records[memory_index]
        memory_messages = messages_with_teacher_memory(
            base_messages_by_index[example_index],
            record,
            prompt_profile,
        )
        memory_prompt = backend.render_messages(memory_messages, add_generation_prompt=True)
        rendered_memory_prompts[(example_index, memory_index)] = memory_prompt
        combined_prompt_tokens = len(_token_ids(tokenizer, memory_prompt, add_special_tokens=False))
        raw_memory_tokens = len(_token_ids(tokenizer, record.experience_text, add_special_tokens=False))
        total_tokens = combined_prompt_tokens + target_token_counts[example_index]
        over_context = total_tokens > context_limit
        preflight_rows.append(
            {
                "state_example_id": _example_id(example_index, example),
                "example_index": example_index,
                "task_id": _example_task_id(example),
                "episode_id": example.episode_id,
                "step_id": example.step_id,
                "candidate_memory_id": record.memory_id,
                "memory_index": memory_index,
                "memory_task_id": record.task_id,
                "candidate_source": pair_sources[(example_index, memory_index)],
                "state_prompt_tokens": l0_prompt_tokens[example_index],
                "raw_memory_tokens": raw_memory_tokens,
                "combined_prompt_tokens": combined_prompt_tokens,
                "target_tokens": target_token_counts[example_index],
                "total_tokens_with_target": total_tokens,
                "context_limit": context_limit,
                "over_context": over_context,
            }
        )
    over_context_rows = [row for row in preflight_rows if row["over_context"]]
    atomic_write_json(
        output_dir / "token_length_preflight.json",
        {
            "format": "raw_text_teacher_token_preflight_v1",
            "context_limit": context_limit,
            "pair_count": len(preflight_rows),
            "over_context_pair_count": len(over_context_rows),
            "over_context_pairs": over_context_rows,
            "rows": preflight_rows,
        },
    )

    labels_path = output_dir / "teacher_labels.jsonl"
    if labels_path.exists():
        labels_path.unlink()
    scored_rows: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []
    scored_pair_counter = 0
    preflight_by_pair = {
        (int(row["example_index"]), int(row["memory_index"])): row for row in preflight_rows
    }
    for example_index, memory_index in sorted(pair_sources):
        preflight = preflight_by_pair[(example_index, memory_index)]
        example = examples[example_index]
        record = records[memory_index]
        row = {
            "format": RAW_TEXT_TEACHER_CACHE_VERSION,
            "state_example_id": _example_id(example_index, example),
            "example_index": example_index,
            "example_jsonl_line": example_index + 1,
            "episode_id": example.episode_id,
            "step_id": example.step_id,
            "task_id": _example_task_id(example),
            "candidate_memory_id": record.memory_id,
            "candidate_memory_index": memory_index,
            "candidate_memory_jsonl_line": memory_index + 1,
            "candidate_memory_task_id": record.task_id,
            "candidate_memory_episode_id": record.episode_id,
            "candidate_source": pair_sources[(example_index, memory_index)],
            "is_proposed_candidate": any(source != "audit_all_memory" for source in pair_sources[(example_index, memory_index)]),
            "is_audit_all_memory_row": "audit_all_memory" in pair_sources[(example_index, memory_index)],
            "L0": l0_by_index[example_index],
            "Lj_text": None,
            "text_utility": None,
            "utility_category": None,
            "state_prompt_tokens": preflight["state_prompt_tokens"],
            "raw_memory_tokens": preflight["raw_memory_tokens"],
            "combined_prompt_tokens": preflight["combined_prompt_tokens"],
            "target_tokens": preflight["target_tokens"],
            "total_tokens_with_target": preflight["total_tokens_with_target"],
            "context_limit": context_limit,
            "over_context": preflight["over_context"],
            "target_sha256": sha256_text(_target_suffix(example)),
            "target_token_sha256": sha256_text(",".join(str(item) for item in target_ids_by_index[example_index])),
            "memory_text_sha256": sha256_text(record.experience_text),
            "renderer_version": renderer_metadata["renderer_version"],
            "renderer_metadata": renderer_metadata,
            "teacher_memory_section_version": TEACHER_MEMORY_SECTION_VERSION,
            "model_name": backend.model_name,
            "checkpoint_identity": f"frozen_hf_pretrained:{backend.model_name}",
            "model_config_commit_hash": getattr(getattr(backend.model, "config", None), "_commit_hash", None),
            "commit_sha": commit_sha,
            "skipped_reason": None,
        }
        if preflight["over_context"]:
            row["skipped_reason"] = "over_context"
            skipped_rows.append(row)
            append_jsonl(labels_path, row)
            continue
        if args.max_score_pairs is not None and scored_pair_counter >= args.max_score_pairs:
            row["skipped_reason"] = "max_score_pairs"
            skipped_rows.append(row)
            append_jsonl(labels_path, row)
            continue
        loss, _prompt_tokens, _target_tokens = _score_mean_target_nll(
            backend,
            rendered_memory_prompts[(example_index, memory_index)],
            target_ids_by_index[example_index],
            target_text_by_index[example_index],
            context_limit,
        )
        utility = l0_by_index[example_index] - loss
        row["Lj_text"] = loss
        row["text_utility"] = utility
        row["utility_category"] = _category(utility)
        scored_rows.append(row)
        scored_pair_counter += 1
        append_jsonl(labels_path, row)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    utility_values = [float(row["text_utility"]) for row in scored_rows if row["text_utility"] is not None]
    memory_lengths = [float(row["raw_memory_tokens"]) for row in scored_rows if row["text_utility"] is not None]
    combined_lengths = [float(row["total_tokens_with_target"]) for row in scored_rows if row["text_utility"] is not None]
    utility_counts = Counter(row["utility_category"] for row in scored_rows)

    audit_rows = [row for row in scored_rows if row["is_audit_all_memory_row"]]
    audit_by_state: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in audit_rows:
        audit_by_state[row["state_example_id"]].append(row)
    audit_state_rows = []
    recall_hits = 0
    for state_id, rows in sorted(audit_by_state.items()):
        best = max(rows, key=lambda row: float(row["text_utility"]))
        proposed_rows = [row for row in rows if row["is_proposed_candidate"]]
        proposed_ids = {row["candidate_memory_id"] for row in proposed_rows}
        hit = best["candidate_memory_id"] in proposed_ids
        recall_hits += int(hit)
        audit_state_rows.append(
            {
                "state_example_id": state_id,
                "legal_scored_memories": len(rows),
                "proposed_scored_memories": len(proposed_rows),
                "highest_utility_memory_id": best["candidate_memory_id"],
                "highest_utility": best["text_utility"],
                "candidate_recalled_highest_utility": hit,
                "candidate_best_utility": max(
                    (float(row["text_utility"]) for row in proposed_rows),
                    default=None,
                ),
            }
        )
    representative_rows: dict[str, Any] = {"positive": None, "neutral": None, "negative": None}
    if scored_rows:
        positives = [row for row in scored_rows if row["utility_category"] == "positive"]
        neutrals = [row for row in scored_rows if row["utility_category"] == "neutral"]
        negatives = [row for row in scored_rows if row["utility_category"] == "negative"]
        if positives:
            representative_rows["positive"] = max(positives, key=lambda row: float(row["text_utility"]))
        if neutrals:
            representative_rows["neutral"] = min(neutrals, key=lambda row: abs(float(row["text_utility"])))
        if negatives:
            representative_rows["negative"] = min(negatives, key=lambda row: float(row["text_utility"]))

    runtime_s = time.perf_counter() - started
    scored_pair_count = max(1, len(scored_rows))
    seconds_per_scored_pair = runtime_s / scored_pair_count
    avg_candidate_count = proposed_pair_count / max(1, len(selected_indices))
    projected_candidate_pairs = int(round(len(examples) * avg_candidate_count))
    projected_full_candidate_seconds = seconds_per_scored_pair * (len(examples) + projected_candidate_pairs)
    projected_all_memory_seconds = seconds_per_scored_pair * (
        len(examples) + sum(len(legal_memory_indices(records, example)) for example in examples)
    )
    summary = {
        "teacher_cache_version": RAW_TEXT_TEACHER_CACHE_VERSION,
        "teacher_memory_section_version": TEACHER_MEMORY_SECTION_VERSION,
        "config": args.config,
        "data": str(data_dir),
        "decision_examples_sha256": sha256_file(data_dir / "decision_examples.jsonl"),
        "memory_records_sha256": sha256_file(data_dir / "memory_records.jsonl"),
        "output_dir": str(output_dir),
        "commit_sha": commit_sha,
        "model_name": backend.model_name,
        "checkpoint_identity": f"frozen_hf_pretrained:{backend.model_name}",
        "context_limit": context_limit,
        "selected_state_count": len(selected_indices),
        "candidate_pair_count": proposed_pair_count,
        "unique_pair_count_including_audit": len(pair_sources),
        "scored_row_count": len(scored_rows),
        "skipped_row_count": len(skipped_rows),
        "over_context_pair_count": len(over_context_rows),
        "over_context_pairs": over_context_rows,
        "utility_counts": dict(utility_counts),
        "utility_distribution": _distribution(utility_values),
        "correlations": {
            "utility_vs_memory_tokens": _pearson(utility_values, memory_lengths),
            "utility_vs_combined_context_tokens": _pearson(utility_values, combined_lengths),
        },
        "audit": {
            "audited_state_count": len(audit_by_state),
            "candidate_recall_hits": recall_hits,
            "candidate_recall_at_proposed": recall_hits / len(audit_by_state) if audit_by_state else None,
            "states": audit_state_rows,
        },
        "runtime_s": runtime_s,
        "seconds_per_scored_pair": seconds_per_scored_pair,
        "projected_full_dataset_candidate_pairs": projected_candidate_pairs,
        "projected_full_dataset_candidate_hours": projected_full_candidate_seconds / 3600.0,
        "projected_full_dataset_all_memory_hours": projected_all_memory_seconds / 3600.0,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "memory_chunk_audit": chunk_audit,
        "representative_rows": representative_rows,
        "pilot_states_path": str(output_dir / "pilot_states.json"),
        "preflight_path": str(output_dir / "token_length_preflight.json"),
        "teacher_labels_path": str(labels_path),
    }
    atomic_write_json(output_dir / "summary.json", summary)
    (output_dir / "report.md").write_text(_render_report(summary), encoding="utf-8")
    print(f"Wrote raw-text teacher pilot to {output_dir}")


if __name__ == "__main__":
    main()
