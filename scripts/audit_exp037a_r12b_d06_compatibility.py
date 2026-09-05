from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.benchmarks.appworld.paired_causal_runtime_14k import (
    resolve_effective_paired_causal_runtime,
)
from rcmf.config import load_config
from rcmf.training.datasets import load_decision_examples
from rcmf.training.procedural_causal_audit_7b import condition_checkpoint_name
from rcmf.training.transition_memory_6a import state_example_id
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file
from scripts.audit_exp037a_14j_first_divergence import _inventory_hash, _tokenizer
from scripts.run_procedural_causal_audit_7b import _messages_for_condition


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def main() -> None:
    args = _parse_args()
    formal_root = args.formal_root.resolve()
    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    if output_root == formal_root or formal_root in output_root.parents:
        raise ValueError("Compatibility output cannot be inside the sealed formal root")
    output_root.mkdir(parents=True, exist_ok=True)
    before = _inventory_hash(formal_root)
    arm_path = formal_root / "resolved_configs/arm_3d.yaml"
    replay_path = source_root / "configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"
    effective, provenance = resolve_effective_paired_causal_runtime(
        replay_config=load_config(replay_path).raw,
        arm_config=load_config(arm_path).raw,
        arm_id="3d",
        arm_config_path=str(arm_path),
        arm_config_sha256=sha256_file(arm_path),
        replay_config_path=str(replay_path),
        replay_config_sha256=sha256_file(replay_path),
    )
    if provenance["three_demo_effective_generation_diff"] != 0:
        raise RuntimeError("Repaired 3D runtime differs from the sealed legacy runtime")

    arm_config = load_config(arm_path).raw
    settings = arm_config["stage_c_7hr"]
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    parent_b = Path(str(settings["parent_exp025b"]))
    examples = {
        state_example_id(index, example): example
        for index, example in enumerate(
            load_decision_examples(corpus / "decision_examples.jsonl")
        )
    }
    transitions = {
        str(row["transition_id"]): row
        for row in _rows(
            parent_b
            / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl"
        )
    }
    signatures = {
        str(row["transition_id"]): row
        for row in _rows(
            parent_b
            / "clean_procedural_audit/clean_transition_signature_manifest.jsonl"
        )
    }
    manifest = _json(formal_root / "arms/3d/paired_causal/condition_manifest.json")
    _tokenizer_value, backend = _tokenizer(source_root, formal_root)
    output_dir = formal_root / "arms/3d/paired_causal/condition_outputs"
    checked = []
    mismatches = []
    started = time.perf_counter()
    for condition in manifest["conditions"]:
        output_path = output_dir / condition_checkpoint_name(
            str(condition["condition_key"])
        )
        if not output_path.exists():
            continue
        sealed = _json(output_path)
        messages = _messages_for_condition(
            condition=condition,
            example=examples[str(condition["state_example_id"])],
            actual_observations=[
                str(value)
                for value in sealed["live_worker"]["actual_replay_observations"]
            ],
            transitions=transitions,
            signatures=signatures,
            prompt_profile=str(
                effective["causal_audit"]["generation"]["prompt_profile"]
            ),
        )
        rendered = backend.render_messages(messages, add_generation_prompt=True)
        tokens = len(
            backend.tokenizer(
                rendered, add_special_tokens=True, truncation=False
            )["input_ids"]
        )
        rendered_sha = hashlib.sha256(rendered.encode()).hexdigest()
        row = {
            "condition_key": str(condition["condition_key"]),
            "state_example_id": str(condition["state_example_id"]),
            "condition_name": str(condition["condition_name"]),
            "sealed_prompt_tokens": int(sealed["prompt_tokens"]),
            "reconstructed_prompt_tokens": tokens,
            "sealed_prompt_sha256": str(sealed["prompt_sha256"]),
            "reconstructed_prompt_sha256": rendered_sha,
            "match": tokens == int(sealed["prompt_tokens"])
            and rendered_sha == str(sealed["prompt_sha256"]),
        }
        checked.append(row)
        if not row["match"]:
            mismatches.append(row)
    if not checked:
        raise RuntimeError("No sealed D06 condition output was available")
    if mismatches:
        raise RuntimeError(f"Repaired 3D prompt behavior changed: {len(mismatches)} rows")
    after = _inventory_hash(formal_root)
    if before != after:
        raise RuntimeError("Sealed 14j root changed during D06 compatibility audit")
    atomic_write_json(
        output_root / "d06_prompt_compatibility.json",
        {
            "format": "exp037a_r12b_d06_prompt_compatibility_v1",
            "effective_runtime": provenance,
            "available_condition_count": len(checked),
            "exact_prompt_match_count": len(checked),
            "mismatch_count": 0,
            "elapsed_seconds": time.perf_counter() - started,
            "qwen_generation_count": 0,
            "appworld_replay_count": 0,
            "target_action_execution_count": 0,
            "formal_root_before": before,
            "formal_root_after": after,
            "formal_root_unchanged": True,
            "rows": checked,
        },
    )
    print(json.dumps({"checked": len(checked), "mismatches": 0}))


if __name__ == "__main__":
    main()
