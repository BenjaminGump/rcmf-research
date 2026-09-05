from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.model.backends.hf_qwen import HFQwenBackend
from rcmf.training.appworld_structured_rescue_7hr import classify_paired_outcome
from rcmf.training.datasets import (
    _appworld_messages_from_example,
    load_decision_examples,
    load_memory_records,
)
from rcmf.training.procedural_causal_audit_7b import (
    LiveBridgeClient,
    build_live_appworld_messages,
    condition_checkpoint_name,
)
from rcmf.training.transition_memory_6a import (
    example_task_id,
    is_legal_transition_pair,
    messages_with_transition_memory,
    state_example_id,
)
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file, sha256_text
from scripts.prepare_appworld_structured_rescue_7hr import _render_and_count
from scripts.run_procedural_causal_audit_7b import (
    _prepare_message,
    _state_contract,
)


DEFAULT_TARGET = "appworld:trace:229360a_3:step:27:line:382"
CONTEXT_LIMIT = 40960


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--target-state", default=DEFAULT_TARGET)
    parser.add_argument("--phase", choices=("static", "known-live", "census"), required=True)
    parser.add_argument("--repeats", type=int, default=2)
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_identity(path: Path) -> dict[str, Any]:
    return {"path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}


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


def _ensure_output_boundary(formal_root: Path, output_root: Path) -> None:
    formal = formal_root.resolve()
    output = output_root.resolve()
    if output == formal or formal in output.parents:
        raise ValueError("Diagnostic output root must not be within the sealed formal root")
    output.mkdir(parents=True, exist_ok=True)


def _selection_rows(root: Path, arm: str) -> dict[str, dict[str, Any]]:
    path = root / f"arms/{arm}/preflight/frozen_train_selections.jsonl"
    rows = _rows(path)
    output = {str(row["state_example_id"]): row for row in rows}
    if len(output) != 499 or len(output) != len(rows):
        raise ValueError(f"Unexpected {arm} selection state count")
    return output


def _memory_increment(row: Mapping[str, Any]) -> int | None:
    if row.get("raw_prompt_tokens") is None:
        return None
    return int(row["raw_prompt_tokens"]) - int(row["base_prompt_tokens"])


def _distribution(values: Sequence[int]) -> dict[str, Any]:
    ordered = sorted(int(value) for value in values)
    if not ordered:
        return {"count": 0}
    quantile = lambda fraction: ordered[round((len(ordered) - 1) * fraction)]
    return {
        "count": len(ordered),
        "minimum": ordered[0],
        "q25": quantile(0.25),
        "median": statistics.median(ordered),
        "q75": quantile(0.75),
        "q90": quantile(0.90),
        "q95": quantile(0.95),
        "maximum": ordered[-1],
        "mean": statistics.fmean(ordered),
    }


def _target_categories(
    three: Mapping[str, Mapping[str, Any]],
    one: Mapping[str, Mapping[str, Any]],
    one_panel: Mapping[str, Any],
    target: str,
) -> dict[str, set[str]]:
    all_one = list(one_panel["state_ids"]) + list(one_panel["expansion_order"])
    target_position = all_one.index(target)
    return {
        "one_demo_scoreable_three_demo_unscoreable": {
            state for state in three if bool(one[state]["scoreable"]) and not bool(three[state]["scoreable"])
        },
        "selected_transition_differs": {
            state
            for state in three
            if three[state].get("selected_transition_id") != one[state].get("selected_transition_id")
        },
        "one_demo_static_raw_headroom_le_6000": {
            state
            for state, row in one.items()
            if row.get("raw_prompt_tokens") is not None
            and CONTEXT_LIMIT - int(row["raw_prompt_tokens"]) <= 6000
        },
        "one_demo_traversal_through_failure_plus_20": set(all_one[: target_position + 21]),
        "three_demo_static_over_context": {
            state for state, row in three.items() if not bool(row["scoreable"])
        },
    }


def _safe_target_row(row: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "state_example_id",
        "state_task_id",
        "state_step_id",
        "model_split",
        "selected_class_id",
        "class_score",
        "class_margin",
        "canonical_transition_id",
        "canonical_transition_legal",
        "canonical_illegal_same_class_substitution",
        "selected_transition_id",
        "same_class_substitution",
        "base_prompt_sha256",
        "base_prompt_tokens",
        "raw_prompt_sha256",
        "raw_prompt_tokens",
        "over_context",
        "scoreable",
        "selector_score_quantile",
        "selector_margin_quantile",
        "predicted_action_stratum",
        "legal_transition_count",
        "legal_class_count",
        "attempts",
    )
    return {key: row.get(key) for key in keys}


def _partial_rows(root: Path, arm: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    manifest = _json(root / f"arms/{arm}/paired_causal/condition_manifest.json")
    output_dir = root / f"arms/{arm}/paired_causal/condition_outputs"
    by_state: dict[str, dict[str, Mapping[str, Any]]] = {}
    output_paths: dict[str, list[str]] = {}
    for condition in manifest["conditions"]:
        path = output_dir / condition_checkpoint_name(str(condition["condition_key"]))
        if not path.exists():
            continue
        row = _json(path)
        state = str(condition["state_example_id"])
        by_state.setdefault(state, {})[str(condition["condition_name"])] = row
        output_paths.setdefault(state, []).append(str(path))
    labels = {}
    for state, conditions in by_state.items():
        if set(conditions) != {"T0_bare", "T1_selected_raw"}:
            continue
        bare = dict(conditions["T0_bare"]["metrics"])
        raw = dict(conditions["T1_selected_raw"]["metrics"])
        bare["action_signature_match"] = bool(bare["canonical_procedural_signature_match"])
        raw["action_signature_match"] = bool(raw["canonical_procedural_signature_match"])
        labels[state] = str(classify_paired_outcome(bare, raw)["label"])
    return labels, output_paths


def _state_fate(
    root: Path,
    arm: str,
    target: str,
    selections: Mapping[str, Mapping[str, Any]],
    panel: Mapping[str, Any],
) -> dict[str, Any]:
    all_ids = list(panel["state_ids"]) + list(panel["expansion_order"])
    position = all_ids.index(target)
    manifest = _json(root / f"arms/{arm}/paired_causal/condition_manifest.json")
    slots = {str(row["state_example_id"]): row for row in manifest["slots"]}
    labels, output_paths = _partial_rows(root, arm)
    missing_dir = root / f"arms/{arm}/paired_causal/replay_missing"
    replay_missing = {
        str(row["state_example_id"]): row
        for path in missing_dir.glob("*.json")
        for row in [_json(path)]
    }
    preceding = Counter(labels[state] for state in all_ids[:position] if state in labels)
    result = {
        "panel_part": "initial" if position < len(panel["state_ids"]) else "expansion",
        "position_zero_based": position,
        "position_one_based": position + 1,
        "expansion_ordinal_zero_based": None if position < len(panel["state_ids"]) else position - len(panel["state_ids"]),
        "slot": slots[target],
        "static_selection": _safe_target_row(selections[target]),
        "condition_outputs": output_paths.get(target, []),
        "paired_label": labels.get(target),
        "replay_missing": replay_missing.get(target),
        "label_counts_before_position": dict(sorted(preceding.items())),
        "completed_pairs_before_position": sum(preceding.values()),
        "replay_missing_before_position": sum(state in replay_missing for state in all_ids[:position]),
        "static_unscoreable_before_position": sum(
            not bool(selections[state]["scoreable"]) for state in all_ids[:position]
        ),
        "states_remaining_after_position": len(all_ids) - position - 1,
    }
    if not bool(slots[target]["scoreable"]):
        result["fate"] = "STATIC_OVER_CONTEXT_SKIPPED"
    elif target in replay_missing:
        result["fate"] = "REPLAY_SEMANTIC_MISSING"
    elif target in labels:
        result["fate"] = "PAIRED_EXECUTED"
    elif len(output_paths.get(target, [])) == 1:
        result["fate"] = "PARTIAL_SINGLE_CONDITION_BEFORE_FATAL"
    else:
        result["fate"] = "NOT_REACHED_OR_NO_OUTPUT"
    return result


def audit_static(
    formal_root: Path, source_root: Path, output_root: Path, target: str
) -> None:
    three = _selection_rows(formal_root, "3d")
    one = _selection_rows(formal_root, "1d")
    if set(three) != set(one):
        raise ValueError("D05/O05 state universes differ")
    state_ids = sorted(three)
    three_panel = _json(formal_root / "arms/3d/preflight/initial_panel.json")
    one_panel = _json(formal_root / "arms/1d/preflight/initial_panel.json")
    classes = {
        str(row["signature_class_id"]): row
        for row in _json(
            formal_root
            / "shared/compat_exp025b/clean_procedural_audit/clean_signature_equivalence_manifest.json"
        )["classes"]
    }
    transitions = {
        str(row["transition_id"]): row
        for row in _rows(
            formal_root
            / "shared/compat_exp025b/clean_cache_rebuild/transition_preflight/transition_manifest.jsonl"
        )
    }
    target_class = classes[str(one[target]["selected_class_id"])]
    class_members = [
        {
            "transition_id": transition_id,
            "teacher_section_tokens": int(transitions[transition_id]["teacher_section_tokens"]),
            "source_task_id": target_class["member_source_task_ids"][index],
            "parent_id": target_class["member_parent_ids"][index],
            "attempted_in_3d": any(
                row["transition_id"] == transition_id for row in three[target]["attempts"]
            ),
            "attempted_in_1d": any(
                row["transition_id"] == transition_id for row in one[target]["attempts"]
            ),
        }
        for index, transition_id in enumerate(target_class["member_transition_ids"])
    ]
    state_hash = sha256_text(target)
    target_payload = {
        "state_id": target,
        "task_id": one[target]["state_task_id"],
        "step": one[target]["state_step_id"],
        "representation_universe": {
            arm: {
                "count": 638,
                "row": _file_identity(
                    formal_root / f"arms/{arm}/representation_cache/multiview/state_rows/{state_hash}.pt"
                ),
            }
            for arm in ("3d", "1d")
        },
        "three_demo": _safe_target_row(three[target]),
        "one_demo": _safe_target_row(one[target]),
        "selected_class_members": class_members,
        "selected_class_member_order": list(target_class["member_transition_ids"]),
        "legal_member_order_from_attempts": {
            "3d": [row["transition_id"] for row in three[target]["attempts"]],
            "1d": [row["transition_id"] for row in one[target]["attempts"]],
        },
        "panel": {
            "3d_initial_index_zero_based": three_panel["state_ids"].index(target),
            "1d_initial_index_zero_based": one_panel["state_ids"].index(target),
            "initial_same_set": set(three_panel["state_ids"]) == set(one_panel["state_ids"]),
            "initial_same_order": three_panel["state_ids"] == one_panel["state_ids"],
        },
        "fates": {
            "D06": _state_fate(formal_root, "3d", target, three, three_panel),
            "O06": _state_fate(formal_root, "1d", target, one, one_panel),
        },
    }
    atomic_write_json(output_root / "phase_a_state_comparison.json", target_payload)

    scoreability = {
        "same": sum(bool(three[s]["scoreable"]) == bool(one[s]["scoreable"]) for s in state_ids),
        "three_demo_only": sum(bool(three[s]["scoreable"]) and not bool(one[s]["scoreable"]) for s in state_ids),
        "one_demo_only": sum(bool(one[s]["scoreable"]) and not bool(three[s]["scoreable"]) for s in state_ids),
        "both_scoreable": sum(bool(three[s]["scoreable"]) and bool(one[s]["scoreable"]) for s in state_ids),
        "both_unscoreable": sum(not bool(three[s]["scoreable"]) and not bool(one[s]["scoreable"]) for s in state_ids),
    }
    comparable = []
    for state in state_ids:
        three_tokens = _memory_increment(three[state])
        one_tokens = _memory_increment(one[state])
        if three_tokens is None or one_tokens is None:
            continue
        comparable.append(
            {
                "state_id": state,
                "three_demo_transition_id": three[state].get("selected_transition_id"),
                "one_demo_transition_id": one[state].get("selected_transition_id"),
                "three_demo_memory_tokens": three_tokens,
                "one_demo_memory_tokens": one_tokens,
                "one_minus_three_memory_tokens": one_tokens - three_tokens,
                "same_selected_class": three[state]["selected_class_id"] == one[state]["selected_class_id"],
            }
        )
    global_payload = {
        "logical_state_count": len(state_ids),
        "state_universes_exact": set(three) == set(one),
        "scoreability": scoreability,
        "selected_class": {
            "same": sum(three[s]["selected_class_id"] == one[s]["selected_class_id"] for s in state_ids),
            "different": sum(three[s]["selected_class_id"] != one[s]["selected_class_id"] for s in state_ids),
        },
        "selected_transition": {
            "same_including_null": sum(three[s].get("selected_transition_id") == one[s].get("selected_transition_id") for s in state_ids),
            "same_non_null": sum(three[s].get("selected_transition_id") is not None and three[s].get("selected_transition_id") == one[s].get("selected_transition_id") for s in state_ids),
            "different": sum(three[s].get("selected_transition_id") != one[s].get("selected_transition_id") for s in state_ids),
        },
        "same_class_substitution": {
            arm: sum(bool(row["same_class_substitution"]) for row in values.values())
            for arm, values in (("3d", three), ("1d", one))
        },
        "memory_token_distributions": {
            arm: _distribution(
                [value for row in values.values() if (value := _memory_increment(row)) is not None]
            )
            for arm, values in (("3d", three), ("1d", one))
        },
        "top_20_one_minus_three_memory_tokens": sorted(
            comparable,
            key=lambda row: (row["one_minus_three_memory_tokens"], row["state_id"]),
            reverse=True,
        )[:20],
    }
    atomic_write_json(output_root / "d05_o05_global_comparison.json", global_payload)

    d06 = _json(formal_root / "arms/3d/paired_causal/paired_outcomes.json")
    o06_labels, o06_outputs = _partial_rows(formal_root, "1d")
    categories = _target_categories(three, one, one_panel, target)
    target_manifest = [
        {
            "state_id": state,
            "categories": sorted(name for name, values in categories.items() if state in values),
        }
        for state in sorted(set().union(*categories.values()))
    ]
    atomic_write_json(
        output_root / "phase_b_target_manifest.json",
        {
            "selection_rule": "predeclared_r12a_targeted_census",
            "category_counts": {name: len(values) for name, values in categories.items()},
            "union_count": len(target_manifest),
            "rows": target_manifest,
        },
    )
    traversal = {
        "D06": {
            "initial_state_count": d06["initial_state_count"],
            "initial_completed_state_count": d06["initial_completed_state_count"],
            "expanded_state_count": d06["expanded_state_count"],
            "paired_rows": d06["state_count"],
            "executed_condition_outputs": d06["executed_condition_output_count"],
            "static_over_context_missing": d06["over_context_missing_count"],
            "replay_semantic_missing": d06["replay_semantic_missing_count"],
            "labels": d06["label_counts"],
            "minimum_label_gate_passed": d06["minimum_label_gate_passed"],
            "maximum_state_space_exhausted": d06["maximum_state_space_exhausted"],
            "final_expansion_ordinal_zero_based": len(three_panel["expansion_order"]) - 1,
        },
        "O06": {
            "initial_state_count": len(one_panel["state_ids"]),
            "completed_paired_rows": len(o06_labels),
            "condition_output_count": sum(len(values) for values in o06_outputs.values()),
            "replay_semantic_missing_before_failure": len(list((formal_root / "arms/1d/paired_causal/replay_missing").glob("*.json"))),
            "partial_labels": dict(sorted(Counter(o06_labels.values()).items())),
            "failing_state_position_zero_based": (list(one_panel["state_ids"]) + list(one_panel["expansion_order"])).index(target),
            "failing_state_panel_part": "initial",
            "states_remaining_after_failure": len(one_panel["state_ids"]) + len(one_panel["expansion_order"]) - (list(one_panel["state_ids"]) + list(one_panel["expansion_order"])).index(target) - 1,
        },
        "answer_label_evolution_caused_reach": False,
        "reason": "Both arms must traverse every initial-panel state before quota stopping is eligible; the target is initial in both arms.",
    }
    atomic_write_json(output_root / "d06_o06_traversal_comparison.json", traversal)
    one_manifest = _json(formal_root / "arms/1d/paired_causal/condition_manifest.json")
    bare_condition = next(
        row
        for row in one_manifest["conditions"]
        if row["state_example_id"] == target and row["condition_name"] == "T0_bare"
    )
    input_paths = [
        formal_root / "arms/3d/preflight/frozen_train_selections.jsonl",
        formal_root / "arms/1d/preflight/frozen_train_selections.jsonl",
        formal_root / "arms/3d/preflight/initial_panel.json",
        formal_root / "arms/1d/preflight/initial_panel.json",
        formal_root / "arms/3d/paired_causal/condition_manifest.json",
        formal_root / "arms/1d/paired_causal/condition_manifest.json",
        formal_root / "arms/3d/paired_causal/paired_outcomes.json",
        formal_root / "gate/d06_three_demo_reproduction_gate.json",
        formal_root / "gate/three_demo_reproduction_gate.json",
        formal_root / "stages/O06_paired_causal_outcomes/failure.json",
        formal_root / "stages/O06_paired_causal_outcomes/completion.json",
        formal_root / "stages/O06_paired_causal_outcomes/process.json",
        formal_root / "stages/O06_paired_causal_outcomes/logs/stdout.log",
        formal_root / "stages/O06_paired_causal_outcomes/logs/stderr.log",
        formal_root
        / "arms/1d/paired_causal/condition_outputs"
        / condition_checkpoint_name(str(bare_condition["condition_key"])),
        formal_root
        / "shared/compat_exp025b/clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        formal_root
        / "shared/compat_exp025b/clean_procedural_audit/clean_signature_equivalence_manifest.json",
        formal_root / f"arms/3d/representation_cache/multiview/state_rows/{state_hash}.pt",
        formal_root / f"arms/1d/representation_cache/multiview/state_rows/{state_hash}.pt",
        formal_root / "resolved_configs/arm_3d.yaml",
        formal_root / "resolved_configs/arm_1d.yaml",
        source_root / "configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml",
        source_root / "rcmf/benchmarks/appworld/reproducible_stages_14b.py",
        source_root / "scripts/prepare_appworld_structured_rescue_7hr.py",
        source_root / "scripts/run_appworld_train_causal_gate_7hr.py",
        source_root / "scripts/run_procedural_causal_audit_7b.py",
        source_root / "rcmf/training/procedural_causal_audit_7b.py",
        source_root / "rcmf/model/backends/hf_qwen.py",
    ]
    missing = [str(path) for path in input_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing audit evidence: {missing}")
    atomic_write_json(
        output_root / "input_artifact_index.json",
        {
            "format": "exp037a_r12a_input_artifact_index_v1",
            "formal_root": str(formal_root),
            "frozen_source_root": str(source_root),
            "artifacts": [_file_identity(path) for path in input_paths],
        },
    )


def _load_examples_and_records(formal_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    config = load_config(formal_root / "resolved_configs/arm_1d.yaml")
    corpus = Path(str(config.raw["stage_c_7hr"]["reconciled_corpus_dir"]))
    examples = {}
    for index, example in enumerate(load_decision_examples(corpus / "decision_examples.jsonl")):
        examples[state_example_id(index, example)] = example
    records = {str(row.task_id): row for row in load_memory_records(corpus / "memory_records.jsonl")}
    return examples, records


def _tokenizer(source_root: Path, formal_root: Path) -> tuple[Any, HFQwenBackend]:
    from transformers import AutoTokenizer

    replay = load_config(source_root / "configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml")
    model_name = str(replay.raw["stage_c_7b"]["causal_audit"]["generation"]["model_name"])
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    backend = HFQwenBackend(
        model_name=model_name,
        enable_thinking=False,
        load_model=False,
    )
    backend.tokenizer = tokenizer
    return tokenizer, backend


def _count_apis(tokenizer: Any, backend: HFQwenBackend, messages: list[dict[str, str]]) -> dict[str, Any]:
    preflight_rendered, preflight_tokens = _render_and_count(tokenizer, messages)
    runtime_rendered = backend.render_messages(messages, add_generation_prompt=True)
    runtime_no_special = len(tokenizer(runtime_rendered, add_special_tokens=False, truncation=False)["input_ids"])
    runtime_special = len(tokenizer(runtime_rendered, add_special_tokens=True, truncation=False)["input_ids"])
    tokenized = backend.tokenize_messages(messages, add_generation_prompt=True)
    return {
        "message_array_sha256": _canonical_hash(messages),
        "preflight_rendered_sha256": sha256_text(preflight_rendered),
        "preflight_render_and_count": preflight_tokens,
        "runtime_rendered_sha256": sha256_text(runtime_rendered),
        "runtime_direct_add_special_tokens_false": runtime_no_special,
        "runtime_direct_add_special_tokens_true": runtime_special,
        "backend_tokenize_messages": int(tokenized.metadata["input_tokens"]),
        "generate_equivalent_input_tokens": int(tokenized.input_ids.shape[1]),
        "preflight_runtime_rendered_equal": preflight_rendered == runtime_rendered,
    }


def _raw_messages(
    messages: list[dict[str, str]], transition: Mapping[str, Any], profile: str
) -> list[dict[str, str]]:
    return messages_with_transition_memory(messages, transition, profile)


def _replay_observations(
    *,
    source_root: Path,
    output_root: Path,
    target: str,
    example: Any,
    record: Any,
    repeat: int,
) -> tuple[list[str], dict[str, Any]]:
    settings = load_config(
        source_root / "configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"
    ).raw["stage_c_7b"]
    condition = {
        "condition_key": sha256_text(f"r12a::{target}::{repeat}"),
        "state_example_id": target,
        "state_task_id": example_task_id(example),
    }
    contract = _state_contract(example, record)
    semantic = source_root / "rcmf/training/appworld_replay_clean_rebuild_7b.py"
    bridge = source_root / "scripts/appworld_live_one_step_bridge_7b.py"
    stderr = output_root / f"lambda_only_replay_repeat_{repeat}.stderr.log"
    client = LiveBridgeClient(
        executable=Path(str(settings["legacy"]["executable"])),
        bridge_script=bridge,
        appworld_root=Path(str(settings["legacy"]["appworld_root"])),
        stderr_path=stderr,
        timeout_seconds=float(settings["replay"]["subprocess_timeout_seconds"]),
    )
    started = time.perf_counter()
    prepare = _prepare_message(
        condition=condition,
        contract=contract,
        settings=settings,
        semantic_path=semantic,
        bridge_attempt=f"r12a-repeat-{repeat}-{time.time_ns()}",
    )
    try:
        ready = client.prepare(prepare)
        observations = [str(value) for value in ready["actual_observations"]]
    finally:
        client.terminate()
    return observations, {
        "repeat": repeat,
        "ready": bool(ready["ready"]),
        "history_semantic_v3_match": bool(ready["history_semantic_v3_match"]),
        "observation_count": len(observations),
        "observation_sha256": [_canonical_hash(value) for value in observations],
        "prepared_state_fingerprint": ready["prepared_state_fingerprint"],
        "elapsed_seconds": time.perf_counter() - started,
        "stderr": _file_identity(stderr),
        "appworld_diagnostic_experiment": str(prepare["experiment_name"]),
        "appworld_diagnostic_output_root": str(
            Path(str(settings["legacy"]["appworld_root"]))
            / "experiments/outputs"
            / str(prepare["experiment_name"])
        ),
        "target_action_executed": False,
        "qwen_generation_count": 0,
    }


def _load_shared(formal_root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    transitions = {
        str(row["transition_id"]): row
        for row in _rows(
            formal_root
            / "shared/compat_exp025b/clean_cache_rebuild/transition_preflight/transition_manifest.jsonl"
        )
    }
    classes = {
        str(row["signature_class_id"]): row
        for row in _json(
            formal_root
            / "shared/compat_exp025b/clean_procedural_audit/clean_signature_equivalence_manifest.json"
        )["classes"]
    }
    return transitions, classes


def _token_matrix(
    *,
    tokenizer: Any,
    backend: HFQwenBackend,
    example: Any,
    observations: Sequence[str],
    transition: Mapping[str, Any],
) -> dict[str, Any]:
    result = {}
    for profile in ("full_demo", "full_demo_first_only"):
        messages = build_live_appworld_messages(
            example,
            observations,
            prompt_profile=profile,
        )
        bare = _count_apis(tokenizer, backend, messages)
        raw = _count_apis(tokenizer, backend, _raw_messages(messages, transition, profile))
        result[profile] = {
            "bare": bare,
            "raw_with_one_demo_selected_memory": raw,
            "bare_headroom": CONTEXT_LIMIT - int(bare["generate_equivalent_input_tokens"]),
            "raw_headroom": CONTEXT_LIMIT - int(raw["generate_equivalent_input_tokens"]),
            "raw_feasible": int(raw["generate_equivalent_input_tokens"]) <= CONTEXT_LIMIT,
        }
    return result


def audit_known_live(
    formal_root: Path,
    source_root: Path,
    output_root: Path,
    target: str,
    repeats: int,
) -> None:
    if repeats < 1:
        raise ValueError("At least one fresh replay repeat is required")
    examples, records = _load_examples_and_records(formal_root)
    transitions, _classes = _load_shared(formal_root)
    one = _selection_rows(formal_root, "1d")
    three = _selection_rows(formal_root, "3d")
    transition_id = str(one[target]["selected_transition_id"])
    if transition_id == "None":
        raise ValueError("Known O06 state has no selected transition")
    if three[target].get("selected_transition_id") is not None:
        raise ValueError("Known D05 state unexpectedly has a selected transition")
    transition = transitions[transition_id]
    tokenizer, backend = _tokenizer(source_root, formal_root)
    example = examples[target]
    record = records[example_task_id(example)]

    manifest = _json(formal_root / "arms/1d/paired_causal/condition_manifest.json")
    bare_condition = next(
        row
        for row in manifest["conditions"]
        if row["state_example_id"] == target and row["condition_name"] == "T0_bare"
    )
    sealed_path = (
        formal_root
        / "arms/1d/paired_causal/condition_outputs"
        / condition_checkpoint_name(str(bare_condition["condition_key"]))
    )
    sealed = _json(sealed_path)
    sealed_observations = [
        str(value) for value in sealed["live_worker"]["actual_replay_observations"]
    ]
    sealed_matrix = _token_matrix(
        tokenizer=tokenizer,
        backend=backend,
        example=example,
        observations=sealed_observations,
        transition=transition,
    )
    fresh = []
    for repeat in range(1, repeats + 1):
        observations, replay = _replay_observations(
            source_root=source_root,
            output_root=output_root,
            target=target,
            example=example,
            record=record,
            repeat=repeat,
        )
        replay["tokens"] = _token_matrix(
            tokenizer=tokenizer,
            backend=backend,
            example=example,
            observations=observations,
            transition=transition,
        )
        fresh.append(replay)

    static = {}
    for profile, arm in (("full_demo", "3d"), ("full_demo_first_only", "1d")):
        messages = _appworld_messages_from_example(example, profile)
        static[profile] = {
            "bare": _count_apis(tokenizer, backend, messages),
            "raw_with_one_demo_selected_memory": _count_apis(
                tokenizer, backend, _raw_messages(messages, transition, profile)
            ),
            "sealed_selection_row": _safe_target_row(
                three[target] if arm == "3d" else one[target]
            ),
        }
    payload = {
        "state_id": target,
        "transition_id": transition_id,
        "three_demo_selected_transition_id": three[target].get("selected_transition_id"),
        "one_demo_selected_transition_id": one[target].get("selected_transition_id"),
        "same_selected_class": three[target]["selected_class_id"] == one[target]["selected_class_id"],
        "same_only_legal_attempted_member": [
            row["transition_id"] for row in three[target]["attempts"]
        ]
        == [row["transition_id"] for row in one[target]["attempts"]],
        "matrix_note": (
            "D05 selected no feasible 3D transition; both matrix columns collapse to the "
            "same sole legal member selected by O05. No distinct 3D selected memory exists."
        ),
        "static_stored_state": static,
        "sealed_o06_live_observations": {
            "source": _file_identity(sealed_path),
            "observation_count": len(sealed_observations),
            "observation_sha256": [_canonical_hash(value) for value in sealed_observations],
            "recorded_prompt_tokens": int(sealed["prompt_tokens"]),
            "recorded_prompt_sha256": str(sealed["prompt_sha256"]),
            "tokens": sealed_matrix,
        },
        "fresh_replay_repeats": fresh,
        "qwen_generation_count": 0,
        "target_action_execution_count": 0,
        "context_limit": CONTEXT_LIMIT,
    }
    atomic_write_json(output_root / "live_token_2x2.json", payload)
    token_contract = {
        "fixed_message_arrays": {
            source: {
                profile: {
                    kind: values
                    for kind, values in profile_values.items()
                    if kind in {"bare", "raw_with_one_demo_selected_memory"}
                }
                for profile, profile_values in matrix.items()
            }
            for source, matrix in {
                "static": static,
                "sealed_live": sealed_matrix,
            }.items()
        },
        "runtime_replay_config_prompt_profile": load_config(
            source_root / "configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"
        ).raw["stage_c_7b"]["causal_audit"]["generation"]["prompt_profile"],
        "resolved_3d_preflight_prompt_profile": load_config(
            formal_root / "resolved_configs/arm_3d.yaml"
        ).raw["stage_c_7hr"]["appworld"]["prompt_profile"],
        "resolved_1d_preflight_prompt_profile": load_config(
            formal_root / "resolved_configs/arm_1d.yaml"
        ).raw["stage_c_7hr"]["appworld"]["prompt_profile"],
        "token_count_contract_mismatch": True,
        "mismatch_kind": "PROMPT_PROFILE_CONFIG_SOURCE_MISMATCH",
    }
    atomic_write_json(output_root / "token_count_api_comparison.json", token_contract)


def _ordered_legal_members(
    *,
    example: Any,
    class_row: Mapping[str, Any],
    transitions: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    all_members = [str(value) for value in class_row["member_transition_ids"]]
    legal = [value for value in all_members if is_legal_transition_pair(example, transitions[value])]
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


def _census_arm(
    *,
    arm: str,
    profile: str,
    row: Mapping[str, Any],
    live_messages: list[dict[str, str]],
    example: Any,
    transitions: Mapping[str, Mapping[str, Any]],
    classes: Mapping[str, Mapping[str, Any]],
    tokenizer: Any,
    backend: HFQwenBackend,
) -> dict[str, Any]:
    bare = _count_apis(tokenizer, backend, live_messages)
    class_id = str(row["selected_class_id"])
    members = _ordered_legal_members(
        example=example,
        class_row=classes[class_id],
        transitions=transitions,
    )
    live_members = []
    for transition_id in members:
        tokens = _count_apis(
            tokenizer,
            backend,
            _raw_messages(live_messages, transitions[transition_id], profile),
        )["generate_equivalent_input_tokens"]
        live_members.append(
            {
                "transition_id": transition_id,
                "tokens": int(tokens),
                "headroom": CONTEXT_LIMIT - int(tokens),
                "feasible": int(tokens) <= CONTEXT_LIMIT,
            }
        )
    selected = row.get("selected_transition_id")
    selected_live = next(
        (member for member in live_members if member["transition_id"] == selected), None
    )
    first_feasible = next((member for member in live_members if member["feasible"]), None)
    return {
        "arm": arm,
        "profile": profile,
        "static_scoreable": bool(row["scoreable"]),
        "static_base_tokens": int(row["base_prompt_tokens"]),
        "static_raw_tokens": row.get("raw_prompt_tokens"),
        "static_selected_class_id": class_id,
        "static_selected_transition_id": selected,
        "live_bare_tokens": int(bare["generate_equivalent_input_tokens"]),
        "live_selected": selected_live,
        "legal_member_count": len(members),
        "live_members": live_members,
        "first_live_feasible_member": first_feasible,
        "alternative_live_feasible_exists": bool(
            first_feasible and first_feasible["transition_id"] != selected
        ),
    }


def audit_census(
    formal_root: Path,
    source_root: Path,
    output_root: Path,
    target: str,
) -> None:
    manifest = _json(output_root / "phase_b_target_manifest.json")
    target_rows = list(manifest["rows"])
    if len(target_rows) >= 499:
        raise ValueError("Targeted census unexpectedly expanded to the full state universe")
    three = _selection_rows(formal_root, "3d")
    one = _selection_rows(formal_root, "1d")
    examples, records = _load_examples_and_records(formal_root)
    transitions, classes = _load_shared(formal_root)
    tokenizer, backend = _tokenizer(source_root, formal_root)
    results = []
    started = time.perf_counter()
    for ordinal, target_row in enumerate(target_rows, start=1):
        state = str(target_row["state_id"])
        example = examples[state]
        try:
            observations, replay = _replay_observations(
                source_root=source_root,
                output_root=output_root,
                target=state,
                example=example,
                record=records[example_task_id(example)],
                repeat=1000 + ordinal,
            )
        except BaseException as error:  # noqa: BLE001 - audit records typed failure only
            results.append(
                {
                    "state_id": state,
                    "task_id": example_task_id(example),
                    "step": int(example.step_id),
                    "categories": list(target_row["categories"]),
                    "replay": {
                        "ready": False,
                        "error_type": type(error).__name__,
                        "error_message_sha256": sha256_text(str(error)),
                        "target_action_executed": False,
                        "qwen_generation_count": 0,
                    },
                    "arms": None,
                    "production_o06_profile": "full_demo",
                    "production_o06_one_demo_selected_transition_tokens": None,
                }
            )
            continue
        arms = {}
        live_messages_by_profile = {}
        for arm, profile, rows in (
            ("3d", "full_demo", three),
            ("1d", "full_demo_first_only", one),
        ):
            live_messages = build_live_appworld_messages(
                example,
                observations,
                prompt_profile=profile,
            )
            live_messages_by_profile[profile] = live_messages
            arms[arm] = _census_arm(
                arm=arm,
                profile=profile,
                row=rows[state],
                live_messages=live_messages,
                example=example,
                transitions=transitions,
                classes=classes,
                tokenizer=tokenizer,
                backend=backend,
            )
        one_transition = one[state].get("selected_transition_id")
        production_tokens = None
        if one_transition is not None:
            production_tokens = int(
                _count_apis(
                    tokenizer,
                    backend,
                    _raw_messages(
                        live_messages_by_profile["full_demo"],
                        transitions[str(one_transition)],
                        "full_demo",
                    ),
                )["generate_equivalent_input_tokens"]
            )
        results.append(
            {
                "state_id": state,
                "task_id": example_task_id(example),
                "step": int(example.step_id),
                "categories": list(target_row["categories"]),
                "replay": replay,
                "arms": arms,
                "production_o06_profile": "full_demo",
                "production_o06_one_demo_selected_transition_tokens": production_tokens,
            }
        )
        if ordinal % 10 == 0:
            print(
                json.dumps(
                    {
                        "completed": ordinal,
                        "total": len(target_rows),
                        "elapsed_seconds": time.perf_counter() - started,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    rows_path = output_root / "targeted_census_rows.jsonl"
    rows_path.write_text(
        "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in results),
        encoding="utf-8",
    )
    one_static_live_mismatch = [
        row
        for row in results
        if row["arms"] is not None
        if row["arms"]["1d"]["static_scoreable"]
        and (
            row["arms"]["1d"]["live_selected"] is None
            or not bool(row["arms"]["1d"]["live_selected"]["feasible"])
        )
    ]
    production_profile_failures = [
        row
        for row in results
        if row["arms"] is not None
        if row["production_o06_one_demo_selected_transition_tokens"] is not None
        and int(row["production_o06_one_demo_selected_transition_tokens"]) > CONTEXT_LIMIT
    ]
    replay_failures = [row for row in results if not bool(row["replay"]["ready"])]
    summary = {
        "targeted_state_count": len(results),
        "full_499_census_run": False,
        "qwen_generation_count": 0,
        "target_action_execution_count": 0,
        "replay_ready_count": len(results) - len(replay_failures),
        "replay_failure_count": len(replay_failures),
        "one_demo_static_scoreable_live_infeasible_count": len(one_static_live_mismatch),
        "production_o06_full_demo_profile_infeasible_count": len(production_profile_failures),
        "one_demo_alternative_live_feasible_count": sum(
            bool(row["arms"]["1d"]["alternative_live_feasible_exists"])
            for row in results
            if row["arms"] is not None
        ),
        "three_demo_alternative_live_feasible_count": sum(
            bool(row["arms"]["3d"]["alternative_live_feasible_exists"])
            for row in results
            if row["arms"] is not None
        ),
        "elapsed_seconds": time.perf_counter() - started,
        "rows": _file_identity(rows_path),
    }
    atomic_write_json(output_root / "targeted_census_summary.json", summary)


def _write_artifact_index(output_root: Path, formal_before: Mapping[str, Any], formal_after: Mapping[str, Any]) -> None:
    files = [
        _file_identity(path)
        for path in sorted(value for value in output_root.iterdir() if value.is_file())
        if path.name != "artifact_index.json" and not path.name.endswith(".stderr.log")
    ]
    atomic_write_json(
        output_root / "artifact_index.json",
        {
            "format": "exp037a_r12a_artifact_index_v1",
            "formal_root_before": formal_before,
            "formal_root_after": formal_after,
            "formal_root_unchanged": formal_before == formal_after,
            "git_safe_files": files,
            "lambda_only_stderr_count": len(list(output_root.glob("*.stderr.log"))),
        },
    )


def main() -> None:
    args = _parse_args()
    formal_root = args.formal_root.resolve()
    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    _ensure_output_boundary(formal_root, output_root)
    before = _inventory_hash(formal_root)
    if args.phase == "static":
        audit_static(formal_root, source_root, output_root, args.target_state)
    elif args.phase == "known-live":
        audit_known_live(
            formal_root,
            source_root,
            output_root,
            args.target_state,
            args.repeats,
        )
    else:
        audit_census(formal_root, source_root, output_root, args.target_state)
    after = _inventory_hash(formal_root)
    if before != after:
        raise RuntimeError("Sealed formal root changed during diagnostic audit")
    _write_artifact_index(output_root, before, after)


if __name__ == "__main__":
    main()
