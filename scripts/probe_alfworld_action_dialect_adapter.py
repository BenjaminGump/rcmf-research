from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from rcmf.benchmarks.alfworld.environment import REACT_PUT_MOVE_BRIDGE_ID
from rcmf.benchmarks.alfworld.portable_adapter_v2 import ALFWorldPortableAdapterV2
from rcmf.benchmarks.alfworld.task_manifest import (
    TASK_MANIFEST_SHA256,
    canonical_sha256,
    load_sealed_task_manifest,
    portable_task_records,
)


PLACEMENT_FAMILIES = frozenset(
    {
        "pick_and_place",
        "pick_clean_then_place",
        "pick_heat_then_place",
        "pick_cool_then_place",
        "pick_two_obj",
    }
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _put_alias(move_action: str) -> str:
    match = re.fullmatch(r"move (.+) to (.+)", move_action)
    if match is None:
        raise ValueError(f"not an official move command: {move_action!r}")
    return f"put {match.group(1)} in/on {match.group(2)}"


def _replay_prefix(
    adapter: ALFWorldPortableAdapterV2,
    task: Any,
    prefix: list[dict[str, Any]],
) -> tuple[Any, list[str]]:
    runtime = adapter.create_runtime(task)
    observations: list[str] = []
    for expected in prefix:
        action = str(expected["action"])
        outcome = adapter.execute_action(runtime, action)
        if outcome["action"] != action or outcome["action_dialect_bridge_applied"]:
            runtime.close()
            raise AssertionError(
                f"official TRAIN prefix was changed by the action bridge: {task.task_id}"
            )
        if outcome["observation"] != expected["post_action_observation"]:
            runtime.close()
            raise AssertionError(
                f"official TRAIN prefix observation mismatch: {task.task_id}: {action}"
            )
        observations.append(str(outcome["observation"]))
    return runtime, observations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe the exact RCMF ReAct-put to ALFWorld-move adapter on TRAIN"
    )
    parser.add_argument("--task-manifest", type=Path, required=True)
    parser.add_argument("--trajectory-corpus", type=Path, required=True)
    parser.add_argument("--task-ids", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--diagnostic-uuid", required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()

    manifest_rows = load_sealed_task_manifest(args.task_manifest)
    task_records = portable_task_records(manifest_rows)
    adapter = ALFWorldPortableAdapterV2(
        task_records=task_records,
        trajectories=(),
        prompt_root=Path(__file__).resolve().parents[1]
        / "assets"
        / "prompts"
        / "alfworld",
        data_root=args.data_root,
        token_counter=lambda _messages, _profile: 0,
        corpus_identity={"probe_only": True},
    )
    task_index = {task.task_id: task for task in task_records["train"]}
    corpus_rows = _read_jsonl(args.trajectory_corpus)
    corpus_index = {str(row["task_id"]): row for row in corpus_rows}
    selected_payload = json.loads(args.task_ids.read_text(encoding="utf-8"))
    selected = (
        selected_payload["task_ids"]
        if isinstance(selected_payload, dict)
        else selected_payload
    )
    selected_ids = [str(item) for item in selected]

    by_family: dict[str, str] = {}
    for task_id in selected_ids:
        family = str(corpus_index[task_id]["task_family"])
        if family in PLACEMENT_FAMILIES:
            if family in by_family:
                raise AssertionError(f"duplicate fixed sanity family: {family}")
            by_family[family] = task_id
    if set(by_family) != PLACEMENT_FAMILIES:
        raise AssertionError(
            f"fixed TRAIN selection lacks placement families: {sorted(by_family)}"
        )

    probes: list[dict[str, Any]] = []
    for family in sorted(by_family):
        task_id = by_family[family]
        task = task_index[task_id]
        corpus = corpus_index[task_id]
        steps = list(corpus["steps"])
        move_indices = [
            index
            for index, step in enumerate(steps)
            if str(step["action"]).startswith("move ")
        ]
        if not move_indices:
            raise AssertionError(f"official corpus lacks move command: {task_id}")
        target_index = move_indices[-1]
        target = steps[target_index]
        move_action = str(target["action"])
        put_action = _put_alias(move_action)

        runtime, prefix_observations = _replay_prefix(
            adapter,
            task,
            steps[:target_index],
        )
        actions_before = len(runtime.actions)
        outcome = adapter.execute_action(runtime, put_action)
        actions_after = len(runtime.actions)
        runtime.close()

        if actions_after != actions_before + 1:
            raise AssertionError(f"adapter did not execute exactly one step: {task_id}")
        if outcome["model_action"] != put_action:
            raise AssertionError(f"adapter did not preserve model action: {task_id}")
        if outcome["action"] != move_action:
            raise AssertionError(f"adapter did not execute exact official move: {task_id}")
        if not outcome["action_dialect_bridge_applied"]:
            raise AssertionError(f"adapter did not report bridge activation: {task_id}")
        if outcome["action_dialect_bridge_id"] != REACT_PUT_MOVE_BRIDGE_ID:
            raise AssertionError(f"adapter reported wrong bridge identity: {task_id}")
        if outcome["observation"] != target["post_action_observation"]:
            raise AssertionError(f"adapter observation differs from official replay: {task_id}")
        if float(outcome["raw_reward"]) != float(target["raw_reward"]):
            raise AssertionError(f"adapter reward differs from official replay: {task_id}")
        if bool(outcome["done"]) != bool(target["terminal"]):
            raise AssertionError(f"adapter done differs from official replay: {task_id}")
        if bool(outcome["official_won"]) != bool(target["official_won"]):
            raise AssertionError(f"adapter won differs from official replay: {task_id}")

        probes.append(
            {
                "family": family,
                "task_id": task_id,
                "game_path": task.metadata["game_path"],
                "official_sequence_sha256": corpus["sequence_sha256"],
                "target_step_index": target_index,
                "prefix_length": target_index,
                "prefix_observations_sha256": canonical_sha256(prefix_observations),
                "model_action": put_action,
                "environment_action": outcome["action"],
                "action_dialect_bridge_applied": outcome[
                    "action_dialect_bridge_applied"
                ],
                "action_dialect_bridge_id": outcome["action_dialect_bridge_id"],
                "environment_observation": outcome["observation"],
                "environment_reward": outcome["raw_reward"],
                "environment_done": outcome["done"],
                "environment_won": outcome["official_won"],
                "expected_official_step": target,
            }
        )

    result: dict[str, Any] = {
        "schema_version": "rcmf_alfworld_action_dialect_adapter_train_probe_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "diagnostic_uuid": args.diagnostic_uuid,
        "source_commit": args.source_commit,
        "scope": "TRAIN-only actual RCMF adapter probe; no tokenizer/model load or generation",
        "task_manifest_file_sha256": _sha256_file(args.task_manifest),
        "task_manifest_canonical_sha256": TASK_MANIFEST_SHA256,
        "trajectory_corpus_sha256": _sha256_file(args.trajectory_corpus),
        "fixed_sanity_task_ids_sha256": _sha256_file(args.task_ids),
        "selection": "pre-existing fixed six-family TRAIN sanity; all five placement families",
        "action_dialect_bridge_id": REACT_PUT_MOVE_BRIDGE_ID,
        "probes": probes,
        "aggregate": {
            "families": len(probes),
            "bridge_activations": sum(
                bool(row["action_dialect_bridge_applied"]) for row in probes
            ),
            "exact_official_environment_actions": sum(
                row["environment_action"]
                == row["expected_official_step"]["action"]
                for row in probes
            ),
            "exact_official_observations": sum(
                row["environment_observation"]
                == row["expected_official_step"]["post_action_observation"]
                for row in probes
            ),
            "official_successes": sum(
                bool(row["environment_done"] and row["environment_won"])
                for row in probes
            ),
        },
        "decision": "RCMF_ACTION_DIALECT_BRIDGE_TRAIN_PROBE_PASS",
        "negative_statements": {
            "tokenizer_loaded": False,
            "model_weights_loaded": False,
            "model_forward": False,
            "model_generation": False,
            "evaluation_split_used": False,
            "prompt_or_model_changed": False,
            "training_run": False,
        },
    }
    result["canonical_identity_sha256"] = canonical_sha256(result)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite probe evidence: {output}")
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
