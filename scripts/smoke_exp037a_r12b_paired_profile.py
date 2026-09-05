from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import time

import _bootstrap  # noqa: F401

from rcmf.benchmarks.appworld.paired_causal_runtime_14k import (
    resolve_effective_paired_causal_runtime,
)
from rcmf.config import load_config
from rcmf.model.backends.hf_qwen import HFQwenBackend
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.procedural_causal_audit_7b import condition_checkpoint_name
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file
from scripts.audit_exp037a_14j_first_divergence import _inventory_hash
from scripts.run_appworld_train_causal_gate_7hr import (
    _build_manifest,
    _examples_by_state,
    _records_by_task,
)
from scripts.run_procedural_causal_audit_7b import _run_condition


TARGET_STATE = "appworld:trace:229360a_3:step:27:line:382"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    return parser.parse_args()


def _rows(path: Path) -> list[dict[str, object]]:
    return [dict(row) for row in read_jsonl(path)]


def main() -> None:
    args = _parse_args()
    formal_root = args.formal_root.resolve()
    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    if output_root == formal_root or formal_root in output_root.parents:
        raise ValueError("Smoke output cannot be inside the sealed formal root")
    output_root.mkdir(parents=True, exist_ok=True)
    before = _inventory_hash(formal_root)
    arm_path = formal_root / "resolved_configs/arm_1d.yaml"
    replay_path = source_root / "configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"
    arm_config = load_config(arm_path).raw
    replay_config = load_config(replay_path).raw
    effective, provenance = resolve_effective_paired_causal_runtime(
        replay_config=replay_config,
        arm_config=arm_config,
        arm_id="1d",
        arm_config_path=str(arm_path),
        arm_config_sha256=sha256_file(arm_path),
        replay_config_path=str(replay_path),
        replay_config_sha256=sha256_file(replay_path),
    )
    if provenance["effective_runtime_prompt_profile"] != "full_demo_first_only":
        raise RuntimeError("Smoke did not resolve the one-demo prompt profile")
    effective_path = output_root / "effective_runtime_config.json"
    atomic_write_json(
        effective_path,
        {
            **provenance,
            "diagnostic_only": True,
            "effective_causal_generation": copy.deepcopy(
                effective["causal_audit"]["generation"]
            ),
        },
    )
    provenance = {
        **provenance,
        "effective_runtime_artifact_path": str(effective_path),
        "effective_runtime_artifact_sha256": sha256_file(effective_path),
    }

    arm_root = formal_root / "arms/1d"
    panel = json.loads(
        (arm_root / "preflight/initial_panel.json").read_text(encoding="utf-8")
    )
    selections = {
        str(row["state_example_id"]): row
        for row in _rows(arm_root / "preflight/frozen_train_selections.jsonl")
    }
    manifest = _build_manifest(panel, selections, provenance)
    conditions = [
        row for row in manifest["conditions"] if row["state_example_id"] == TARGET_STATE
    ]
    if [row["condition_name"] for row in conditions] != [
        "T0_bare",
        "T1_selected_raw",
    ]:
        raise RuntimeError("Known target did not resolve to one paired condition")
    manifest_path = output_root / "condition_manifest.json"
    atomic_write_json(manifest_path, manifest)

    settings = arm_config["stage_c_7hr"]
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    parent_b = Path(str(settings["parent_exp025b"]))
    examples = _examples_by_state(
        load_decision_examples(corpus / "decision_examples.jsonl")
    )
    records = _records_by_task(load_memory_records(corpus / "memory_records.jsonl"))
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
    generation = effective["causal_audit"]["generation"]
    backend = HFQwenBackend(
        model_name=str(generation["model_name"]),
        dtype=str(generation["dtype"]),
        device_map=generation.get("device_map"),
        freeze_backbone=True,
        enable_thinking=False,
        load_model=True,
    )
    backend.model.eval()
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Smoke loaded trainable Qwen parameters")
    slot = next(
        row for row in manifest["slots"] if row["state_example_id"] == TARGET_STATE
    )
    started = time.perf_counter()
    outputs = []
    for ordinal, condition in enumerate(conditions, start=1):
        output_path = output_root / "condition_outputs" / condition_checkpoint_name(
            str(condition["condition_key"])
        )
        row, reused = _run_condition(
            condition=condition,
            output_path=output_path,
            stderr_path=output_root
            / "worker_logs"
            / f"{condition_checkpoint_name(str(condition['condition_key']))}.stderr.log",
            attempt_id=args.attempt_id,
            ordinal=ordinal,
            settings=effective,
            config_sha256=sha256_file(manifest_path),
            corpus_lineage_sha256=str(settings["expected_replay_lineage_sha256"]),
            condition_manifest=manifest,
            example=examples[TARGET_STATE],
            record=records[str(slot["state_task_id"])],
            transitions=transitions,
            signatures=signatures,
            raw_utility={},
            backend=backend,
            semantic_path=source_root
            / "rcmf/training/appworld_replay_clean_rebuild_7b.py",
            bridge_script=source_root / "scripts/appworld_live_one_step_bridge_7b.py",
            runtime_provenance=provenance,
        )
        if reused:
            raise RuntimeError("Primary smoke unexpectedly reused an output")
        if row["paired_causal_runtime"] != provenance:
            raise RuntimeError("Condition output did not seal runtime provenance")
        outputs.append(
            {
                "condition_name": row["condition_name"],
                "condition_key": row["condition_key"],
                "prompt_tokens": row["prompt_tokens"],
                "prompt_sha256": row["prompt_sha256"],
                "output_path": str(output_path),
                "output_sha256": sha256_file(output_path),
            }
        )
    after = _inventory_hash(formal_root)
    if before != after:
        raise RuntimeError("Sealed 14j root changed during paired-condition smoke")
    atomic_write_json(
        output_root / "summary.json",
        {
            "format": "exp037a_r12b_paired_profile_smoke_v1",
            "diagnostic_only": True,
            "state_id": TARGET_STATE,
            "condition_count": len(outputs),
            "generated_condition_count": len(outputs),
            "effective_runtime_prompt_profile": provenance[
                "effective_runtime_prompt_profile"
            ],
            "qwen_frozen": True,
            "optimizer_steps": 0,
            "backward_count": 0,
            "elapsed_seconds": time.perf_counter() - started,
            "formal_root_before": before,
            "formal_root_after": after,
            "formal_root_unchanged": True,
            "outputs": outputs,
        },
    )
    print(json.dumps({"state": TARGET_STATE, "conditions": len(outputs)}))


if __name__ == "__main__":
    main()
