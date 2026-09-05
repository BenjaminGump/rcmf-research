from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
from transformers import AutoTokenizer

from rcmf.config import load_config
from rcmf.model.backends.hf_qwen import HFQwenBackend
from rcmf.training.datasets import (
    _appworld_messages_from_example,
    load_decision_examples,
)
from rcmf.training.transition_memory_6a import (
    is_legal_transition_pair,
    messages_with_transition_memory,
    state_example_id,
)
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_text
from scripts.prepare_appworld_structured_rescue_7hr import _render_and_count


CONTEXT_LIMIT = 40_960
PROFILES = {"3d": "full_demo", "1d": "full_demo_first_only"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _inventory_hash(root: Path) -> dict[str, Any]:
    rows = []
    for path in sorted(value for value in root.rglob("*") if value.is_file()):
        stat = path.stat()
        rows.append(
            {
                "path": str(path.relative_to(root)),
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    return {"file_count": len(rows), "sha256": _canonical_hash(rows)}


def _ordered_legal_members(
    *,
    example: Any,
    class_row: Mapping[str, Any],
    transitions: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    all_members = [str(value) for value in class_row["member_transition_ids"]]
    legal = [
        value
        for value in all_members
        if is_legal_transition_pair(example, transitions[value])
    ]
    canonical = str(class_row["canonical_transition_id"])
    median = statistics.median(
        int(transitions[value]["teacher_section_tokens"]) for value in all_members
    )
    return ([canonical] if canonical in legal else []) + sorted(
        (value for value in legal if value != canonical),
        key=lambda value: (
            abs(int(transitions[value]["teacher_section_tokens"]) - median),
            sha256_text(value),
        ),
    )


def _count_current(tokenizer: Any, messages: Sequence[Mapping[str, str]]) -> tuple[str, int]:
    return _render_and_count(tokenizer, messages)


def _count_runtime(
    backend: HFQwenBackend, messages: Sequence[Mapping[str, str]]
) -> tuple[str, int]:
    rendered = backend.render_messages(list(messages), add_generation_prompt=True)
    tokenized = backend.tokenize_messages(list(messages), add_generation_prompt=True)
    return rendered, int(tokenized.metadata["input_tokens"])


def _selection_for_counter(
    *,
    base_messages: list[dict[str, str]],
    canonical_transition_id: str,
    members: Sequence[str],
    transitions: Mapping[str, Mapping[str, Any]],
    prompt_profile: str,
    count: Any,
) -> dict[str, Any]:
    rendered, base_tokens = count(base_messages)
    attempts = []
    selected = None
    raw_rendered = None
    raw_tokens = None
    for transition_id in members:
        raw_messages = messages_with_transition_memory(
            base_messages, transitions[transition_id], prompt_profile
        )
        candidate_rendered, candidate_tokens = count(raw_messages)
        attempts.append(
            {"transition_id": transition_id, "prompt_tokens": int(candidate_tokens)}
        )
        if int(candidate_tokens) <= CONTEXT_LIMIT:
            selected = transition_id
            raw_rendered = candidate_rendered
            raw_tokens = int(candidate_tokens)
            break
    return {
        "base_prompt_sha256": sha256_text(rendered),
        "base_prompt_tokens": int(base_tokens),
        "selected_transition_id": selected,
        "raw_prompt_sha256": sha256_text(raw_rendered) if raw_rendered else None,
        "raw_prompt_tokens": raw_tokens,
        "scoreable": selected is not None,
        "over_context": selected is None,
        "same_class_substitution": selected is not None and selected != canonical_transition_id,
        "attempts": attempts,
    }


def main() -> None:
    args = _parse_args()
    formal_root = args.formal_root.resolve()
    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    if output_root == formal_root or formal_root in output_root.parents:
        raise ValueError("R12B diagnostics must not write under the sealed formal root")
    output_root.mkdir(parents=True, exist_ok=True)
    before = _inventory_hash(formal_root)

    arm_configs = {
        arm: load_config(formal_root / f"resolved_configs/arm_{arm}.yaml")
        for arm in PROFILES
    }
    corpus = Path(
        str(arm_configs["1d"].raw["stage_c_7hr"]["reconciled_corpus_dir"])
    )
    examples = {
        state_example_id(index, example): example
        for index, example in enumerate(
            load_decision_examples(corpus / "decision_examples.jsonl")
        )
    }
    settings = arm_configs["1d"].raw["stage_c_7hr"]
    parent_b = Path(str(settings["parent_exp025b"]))
    transitions = {
        str(row["transition_id"]): row
        for row in _rows(
            parent_b
            / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl"
        )
    }
    classes = {
        str(row["signature_class_id"]): row
        for row in _json(
            parent_b / "clean_procedural_audit/clean_signature_equivalence_manifest.json"
        )["classes"]
    }
    replay = load_config(
        source_root / "configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"
    ).raw["stage_c_7b"]
    model_name = str(replay["causal_audit"]["generation"]["model_name"])
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    backend = HFQwenBackend(
        model_name=model_name, enable_thinking=False, load_model=False
    )
    backend.tokenizer = tokenizer

    results = []
    summary: dict[str, Any] = {
        "format": "exp037a_r12b_token_contract_summary_v1",
        "context_limit": CONTEXT_LIMIT,
        "arms": {},
    }
    for arm, expected_profile in PROFILES.items():
        config_profile = str(
            arm_configs[arm].raw["stage_c_7hr"]["appworld"]["prompt_profile"]
        )
        if config_profile != expected_profile:
            raise ValueError(f"Resolved {arm} profile differs: {config_profile}")
        sealed_rows = {
            str(row["state_example_id"]): row
            for row in _rows(
                formal_root / f"arms/{arm}/preflight/frozen_train_selections.jsonl"
            )
        }
        if len(sealed_rows) != 499:
            raise ValueError(f"Expected 499 sealed {arm} selections")
        arm_results = []
        for state_id, sealed in sealed_rows.items():
            example = examples[state_id]
            class_id = str(sealed["selected_class_id"])
            members = _ordered_legal_members(
                example=example,
                class_row=classes[class_id],
                transitions=transitions,
            )
            base_messages = _appworld_messages_from_example(example, expected_profile)
            current = _selection_for_counter(
                base_messages=base_messages,
                canonical_transition_id=str(classes[class_id]["canonical_transition_id"]),
                members=members,
                transitions=transitions,
                prompt_profile=expected_profile,
                count=lambda messages: _count_current(tokenizer, messages),
            )
            runtime = _selection_for_counter(
                base_messages=base_messages,
                canonical_transition_id=str(classes[class_id]["canonical_transition_id"]),
                members=members,
                transitions=transitions,
                prompt_profile=expected_profile,
                count=lambda messages: _count_runtime(backend, messages),
            )
            sealed_projection = {
                key: sealed.get(key)
                for key in (
                    "base_prompt_sha256",
                    "base_prompt_tokens",
                    "selected_transition_id",
                    "raw_prompt_sha256",
                    "raw_prompt_tokens",
                    "scoreable",
                    "over_context",
                    "same_class_substitution",
                    "attempts",
                )
            }
            if current != sealed_projection:
                raise ValueError(f"Current recount differs from sealed {arm} row: {state_id}")
            discrete_changed = any(
                current[key] != runtime[key]
                for key in (
                    "selected_transition_id",
                    "scoreable",
                    "over_context",
                    "same_class_substitution",
                )
            )
            arm_results.append(
                {
                    "state_id": state_id,
                    "selected_class_id": class_id,
                    "current": current,
                    "runtime_equivalent": runtime,
                    "base_token_delta": runtime["base_prompt_tokens"]
                    - current["base_prompt_tokens"],
                    "attempt_token_deltas": [
                        int(right["prompt_tokens"]) - int(left["prompt_tokens"])
                        for left, right in zip(
                            current["attempts"], runtime["attempts"], strict=True
                        )
                    ],
                    "discrete_changed": discrete_changed,
                }
            )
        results.extend({"arm": arm, **row} for row in arm_results)
        changed = [row for row in arm_results if row["discrete_changed"]]
        summary["arms"][arm] = {
            "prompt_profile": expected_profile,
            "state_count": len(arm_results),
            "sealed_recount_exact_count": len(arm_results),
            "discrete_change_count": len(changed),
            "discrete_change_state_ids": [row["state_id"] for row in changed],
            "base_token_delta_counts": {
                str(value): sum(row["base_token_delta"] == value for row in arm_results)
                for value in sorted({row["base_token_delta"] for row in arm_results})
            },
            "attempt_token_delta_counts": {
                str(value): sum(
                    delta == value
                    for row in arm_results
                    for delta in row["attempt_token_deltas"]
                )
                for value in sorted(
                    {
                        delta
                        for row in arm_results
                        for delta in row["attempt_token_deltas"]
                    }
                )
            },
        }

    rows_path = output_root / "token_contract_rows.jsonl"
    rows_path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n"
            for row in results
        ),
        encoding="utf-8",
    )
    summary["total_state_arm_rows"] = len(results)
    summary["zero_discrete_changes"] = all(
        value["discrete_change_count"] == 0 for value in summary["arms"].values()
    )
    summary["rows_sha256"] = sha256_text(rows_path.read_text(encoding="utf-8"))
    after = _inventory_hash(formal_root)
    summary["formal_root_before"] = before
    summary["formal_root_after"] = after
    summary["formal_root_unchanged"] = before == after
    if before != after:
        raise RuntimeError("Sealed formal root changed during R12B static recount")
    atomic_write_json(output_root / "token_contract_summary.json", summary)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
