from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.datasets import load_decision_examples
from rcmf.training.procedural_causal_audit_7b import build_live_appworld_messages
from rcmf.training.transition_memory_6a import messages_with_transition_memory
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file
from scripts.run_appworld_structured_compiler_7hr import (
    _condition_path,
    _topk_teacher,
)
from scripts.run_direct_injection_channel_7dh import _build_backend_from_generation
from scripts.run_procedural_causal_audit_7b import _examples_by_state


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--replay-config", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    replay = load_config(args.replay_config).raw["stage_c_7b"]
    settings = cfg.raw["stage_c_7hr"]
    prompt_profile = str(cfg.benchmark.prompt_profile)
    if prompt_profile != "full_demo_first_only":
        raise RuntimeError("O07 smoke did not receive the arm-resolved one-demo profile")
    outcomes_path = args.artifact_dir / "paired_causal/paired_outcomes.json"
    outcomes = _json(outcomes_path)
    if not outcomes.get("rows"):
        raise RuntimeError("Repaired O06 produced no paired rows")
    paired = outcomes["rows"][0]
    state_id = str(paired["state_example_id"])
    condition_root = args.artifact_dir / "paired_causal/condition_outputs"
    bare_path = _condition_path(
        condition_root, str(paired["bare_condition_key"])
    )
    raw_path = _condition_path(condition_root, str(paired["raw_condition_key"]))
    bare = _json(bare_path)
    raw = _json(raw_path)
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    parent_b = Path(str(settings["parent_exp025b"]))
    examples = _examples_by_state(
        load_decision_examples(corpus / "decision_examples.jsonl")
    )
    transitions = {
        str(row["transition_id"]): row
        for row in _rows(
            parent_b
            / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl"
        )
    }
    observations = [
        str(value) for value in raw["live_worker"]["actual_replay_observations"]
    ]
    base_messages = build_live_appworld_messages(
        examples[state_id], observations, prompt_profile=prompt_profile
    )
    transition_id = str(paired["selected_transition_id"])
    raw_messages = messages_with_transition_memory(
        base_messages, transitions[transition_id], prompt_profile
    )
    backend = _build_backend_from_generation(replay["causal_audit"]["generation"])
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("O07 smoke loaded trainable Qwen parameters")
    started = time.perf_counter()
    top_k = int(settings["compiler"]["top_k"])
    bare_teacher, _bare_ids = _topk_teacher(
        backend=backend,
        messages=base_messages,
        response_text=str(bare["raw_model_response"]),
        top_k=top_k,
    )
    raw_teacher, _raw_ids = _topk_teacher(
        backend=backend,
        messages=raw_messages,
        response_text=str(raw["raw_model_response"]),
        top_k=top_k,
    )
    args.output_root.mkdir(parents=True, exist_ok=False)
    atomic_write_json(
        args.output_root / "summary.json",
        {
            "format": "exp037a_r12b_o07_teacher_smoke_v1",
            "diagnostic_only": True,
            "scientific_result_eligible": False,
            "state_id": state_id,
            "selected_transition_id": transition_id,
            "arm_resolved_prompt_profile": prompt_profile,
            "paired_outcomes": {
                "path": str(outcomes_path),
                "sha256": sha256_file(outcomes_path),
            },
            "condition_outputs": [
                {"path": str(bare_path), "sha256": sha256_file(bare_path)},
                {"path": str(raw_path), "sha256": sha256_file(raw_path)},
            ],
            "bare_prompt_tokens": int(bare_teacher["prompt_tokens"]),
            "raw_prompt_tokens": int(raw_teacher["prompt_tokens"]),
            "bare_response_text_sha256": str(
                bare_teacher["response_text_sha256"]
            ),
            "raw_response_text_sha256": str(raw_teacher["response_text_sha256"]),
            "bare_generated_token_count": int(
                bare_teacher["generated_token_count"]
            ),
            "raw_generated_token_count": int(raw_teacher["generated_token_count"]),
            "top_k": top_k,
            "qwen_frozen": True,
            "qwen_generation_count": 0,
            "optimizer_steps": 0,
            "backward_count": 0,
            "elapsed_seconds": time.perf_counter() - started,
        },
    )
    print(json.dumps({"state": state_id, "prompt_profile": prompt_profile}))


if __name__ == "__main__":
    main()
