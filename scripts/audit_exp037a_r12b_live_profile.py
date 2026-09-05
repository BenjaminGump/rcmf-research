from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.benchmarks.appworld.paired_causal_runtime_14k import (
    resolve_effective_paired_causal_runtime,
)
from rcmf.config import load_config
from rcmf.training.procedural_causal_audit_7b import build_live_appworld_messages
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.audit_exp037a_14j_first_divergence import (
    CONTEXT_LIMIT,
    _count_apis,
    _inventory_hash,
    _load_examples_and_records,
    _load_shared,
    _raw_messages,
    _replay_observations,
    _selection_rows,
    _tokenizer,
)


TARGET_STATE = "appworld:trace:229360a_3:step:27:line:382"
WRONG_PROFILE_STATES = (
    TARGET_STATE,
    "appworld:trace:229360a_2:step:16:line:354",
    "appworld:trace:afc0fce_1:step:20:line:76",
    "appworld:trace:afc0fce_1:step:23:line:79",
    "appworld:trace:afc0fce_1:step:24:line:80",
    "appworld:trace:afc0fce_1:step:18:line:74",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def _resolved_runtime(formal_root: Path, source_root: Path) -> dict[str, Any]:
    arm_path = formal_root / "resolved_configs/arm_1d.yaml"
    replay_path = source_root / "configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"
    _effective, provenance = resolve_effective_paired_causal_runtime(
        replay_config=load_config(replay_path).raw,
        arm_config=load_config(arm_path).raw,
        arm_id="1d",
        arm_config_path=str(arm_path),
        arm_config_sha256=sha256_file(arm_path),
        replay_config_path=str(replay_path),
        replay_config_sha256=sha256_file(replay_path),
    )
    if provenance["effective_runtime_prompt_profile"] != "full_demo_first_only":
        raise RuntimeError("Repaired runtime did not resolve the one-demo profile")
    if provenance["changed_execution_fields"] != ["prompt_profile"]:
        raise RuntimeError("Repaired one-demo runtime changed an unexpected field")
    return provenance


def main() -> None:
    args = _parse_args()
    formal_root = args.formal_root.resolve()
    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    if output_root == formal_root or formal_root in output_root.parents:
        raise ValueError("Diagnostic output cannot be inside the sealed formal root")
    output_root.mkdir(parents=True, exist_ok=True)
    before = _inventory_hash(formal_root)
    provenance = _resolved_runtime(formal_root, source_root)
    examples, records = _load_examples_and_records(formal_root)
    transitions, _classes = _load_shared(formal_root)
    selections = _selection_rows(formal_root, "1d")
    tokenizer, backend = _tokenizer(source_root, formal_root)
    started = time.perf_counter()
    rows = []
    for ordinal, state_id in enumerate(WRONG_PROFILE_STATES, start=1):
        example = examples[state_id]
        transition_id = str(selections[state_id]["selected_transition_id"])
        observations, replay = _replay_observations(
            source_root=source_root,
            output_root=output_root,
            target=state_id,
            example=example,
            record=records[str(selections[state_id]["state_task_id"])],
            repeat=2000 + ordinal,
        )
        profiles = {}
        for profile in ("full_demo", "full_demo_first_only"):
            messages = build_live_appworld_messages(
                example, observations, prompt_profile=profile
            )
            bare = _count_apis(tokenizer, backend, messages)
            raw = _count_apis(
                tokenizer,
                backend,
                _raw_messages(messages, transitions[transition_id], profile),
            )
            profiles[profile] = {
                "bare": bare,
                "raw": raw,
                "raw_tokens": int(raw["generate_equivalent_input_tokens"]),
                "raw_headroom": CONTEXT_LIMIT
                - int(raw["generate_equivalent_input_tokens"]),
                "raw_feasible": int(raw["generate_equivalent_input_tokens"])
                <= CONTEXT_LIMIT,
            }
        if profiles["full_demo"]["raw_feasible"]:
            raise RuntimeError(f"Wrong-profile control unexpectedly feasible: {state_id}")
        if not profiles["full_demo_first_only"]["raw_feasible"]:
            raise RuntimeError(f"Corrected one-demo profile infeasible: {state_id}")
        rows.append(
            {
                "state_id": state_id,
                "selected_transition_id": transition_id,
                "effective_runtime_prompt_profile": provenance[
                    "effective_runtime_prompt_profile"
                ],
                "replay": replay,
                "profiles": profiles,
                "qwen_generation_count": 0,
                "target_action_execution_count": 0,
            }
        )
    target = rows[0]["profiles"]
    if target["full_demo"]["raw_tokens"] != 42927:
        raise RuntimeError("Known-target full-demo runtime count changed")
    if target["full_demo_first_only"]["raw_tokens"] != 38078:
        raise RuntimeError("Known-target one-demo runtime count changed")
    after = _inventory_hash(formal_root)
    if before != after:
        raise RuntimeError("Sealed 14j formal root changed during R12B audit")
    atomic_write_json(
        output_root / "known_target_and_six_state_live_profile.json",
        {
            "format": "exp037a_r12b_known_target_and_six_state_live_profile_v1",
            "effective_runtime": provenance,
            "context_limit": CONTEXT_LIMIT,
            "state_count": len(rows),
            "corrected_profile_feasible_count": sum(
                row["profiles"]["full_demo_first_only"]["raw_feasible"]
                for row in rows
            ),
            "wrong_profile_over_context_count": sum(
                not row["profiles"]["full_demo"]["raw_feasible"] for row in rows
            ),
            "elapsed_seconds": time.perf_counter() - started,
            "qwen_generation_count": 0,
            "target_action_execution_count": 0,
            "formal_root_before": before,
            "formal_root_after": after,
            "formal_root_unchanged": True,
            "rows": rows,
        },
    )
    print(
        json.dumps(
            {
                "states": len(rows),
                "corrected_feasible": len(rows),
                "wrong_profile_over_context": len(rows),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
