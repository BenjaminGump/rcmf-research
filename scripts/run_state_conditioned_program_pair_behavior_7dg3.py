from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.model.backends.hf_qwen import HFQwenBackend
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.oracle_decoder_5fc import (
    LinearDeltaDecoder,
    module_state_sha256,
)
from rcmf.training.procedural_causal_analysis_7b import (
    comparison_set,
    condition_summary,
    per_task_summary,
)
from rcmf.training.procedural_causal_audit_7b import condition_checkpoint_name
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_program_direct_7dg import (
    require_global_seed,
    seed_everything,
)
from rcmf.training.state_conditioned_program_pair_behavior_7dg3 import (
    GLOBAL_SEED,
    PAIR_BEHAVIOR_CONDITIONS,
    build_pair_behavior_manifest,
    pair_behavior_gate,
    runtime_projection,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)
from scripts.run_procedural_causal_audit_7b import (
    _examples_by_state,
    _records_by_task,
)
from scripts.run_state_conditioned_program_direct_7dg import (
    PAIRMLP_NAME,
    _model,
)
from scripts.run_state_conditioned_program_fast_7df import K_TOKENS, LATENT_DIM
from scripts.run_state_conditioned_program_fast_one_step_7df import (
    _build_injector,
    _f3_rows,
    _load_parent_rows,
    _run_condition,
)

ROOT_NAME = "one_step"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/"
            "stage_c_state_conditioned_program_pair_behavior_7dg3.yaml"
        ),
    )
    parser.add_argument(
        "--replay-config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("preflight", "formal", "analyze"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--tmux-session", default="exp025dg3")
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _paths(
    direct: Mapping[str, Any],
    run: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Path]:
    parent_b = Path(str(direct["parent_exp025b"]))
    parent_c = Path(str(direct["parent_exp025c"]))
    parent_cr = Path(str(direct["parent_exp025cr"]))
    corpus = Path(str(direct["reconciled_corpus_dir"]))
    parent_direct = Path(str(run["parent_direct"]))
    parent_g2 = Path(str(run["parent_g2"]))
    return {
        "checkpoint": parent_direct / "pairmlp/checkpoints/model_u08.pt",
        "training_summary": parent_direct / "pairmlp/training_summary.json",
        "pairmlp_evaluation": parent_direct / "pairmlp/final_evaluation_summary.json",
        "parent_direct_final": parent_direct / "final_exp025dg_summary.json",
        "parent_direct_attempts": parent_direct / "attempts.jsonl",
        "parent_g2_final": parent_g2 / "final_exp025dg2_summary.json",
        "parent_g2_attempts": parent_g2 / "attempts.jsonl",
        "state_cache": parent_c / "representation_cache/multiview/state_multiview.pt",
        "transition_cache": (
            parent_c / "representation_cache/multiview/transition_multiview.pt"
        ),
        "selector": parent_c / "selector/ensemble_scores.pt",
        "selector_conditions": parent_cr / "selector_condition_manifest.json",
        "parent_c0_outputs": parent_b / "condition_outputs",
        "parent_f3_outputs": parent_cr / "selector_condition_outputs",
        "parent_smoke": parent_b / "lifecycle_smoke/smoke_summary.json",
        "replay_lineage": parent_b / "replay_validated_corpus_manifest.json",
        "corpus_summary": corpus / "summary.json",
        "decisions": corpus / "decision_examples.jsonl",
        "memories": corpus / "memory_records.jsonl",
        "semantic_module": Path("rcmf/training/appworld_replay_clean_rebuild_7b.py"),
        "bridge_script": Path("scripts/appworld_live_one_step_bridge_7b.py"),
        "manifest": artifact_dir / ROOT_NAME / "condition_manifest.json",
        "latents": artifact_dir / ROOT_NAME / "pairmlp_latents.pt",
        "preflight": artifact_dir / ROOT_NAME / "preflight.json",
        "generation": artifact_dir / ROOT_NAME / "generation_summary.json",
        "analysis": artifact_dir / ROOT_NAME / "analysis.json",
    }


def _require_paths(paths: Mapping[str, Path], names: Sequence[str]) -> None:
    missing = {name: str(paths[name]) for name in names if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing EXP-025D-G3 inputs: {missing}")


def _successful_attempt(path: Path, attempt_id: str, run_uuid: str) -> bool:
    return any(
        str(row.get("attempt_id")) == str(attempt_id)
        and str(row.get("run_uuid")) == str(run_uuid)
        and str(row.get("event")) == "end"
        and int(row.get("exit_code", 1)) == 0
        for row in read_jsonl(path)
    )


def _load_pairmlp(
    *,
    checkpoint_path: Path,
    direct: Mapping[str, Any],
    transition_view_names: Sequence[str],
) -> tuple[torch.nn.Module, LinearDeltaDecoder, dict[str, Any]]:
    payload = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    model = _model(
        PAIRMLP_NAME,
        settings=direct,
        transition_view_names=transition_view_names,
        device=torch.device("cpu"),
    )
    model.load_state_dict(payload["model_state_dict"])
    output_dim = int(payload["decoder_state_dict"]["linear.weight"].shape[0])
    decoder = LinearDeltaDecoder(LATENT_DIM, output_dim)
    decoder.load_state_dict(payload["decoder_state_dict"])
    if decoder.linear.bias is not None:
        raise ValueError("PairMLP private decoder must remain no-bias")
    model.eval()
    decoder.eval()
    return model, decoder, payload


def _validate_checkpoint(
    *,
    payload: Mapping[str, Any],
    model: torch.nn.Module,
    decoder: LinearDeltaDecoder,
    run: Mapping[str, Any],
    training: Mapping[str, Any],
) -> dict[str, Any]:
    update_counts = [int(value) for value in payload["update_counts"]]
    checks = {
        "format": str(payload.get("format"))
        == "direct_behavior_program_checkpoint_7dg_v1",
        "model_name": str(payload.get("model_name")) == PAIRMLP_NAME,
        "global_seed": int(payload.get("global_seed", -1)) == GLOBAL_SEED,
        "completed_rounds": int(payload.get("completed_rounds", -1)) == 8,
        "training_pair_count": len(payload.get("pair_ids", [])) == 479,
        "unique_training_pairs": len(set(payload.get("pair_ids", []))) == 479,
        "exactly_eight_updates_each": len(update_counts) == 479
        and set(update_counts) == {8},
        "selected_checkpoint": int(training["selected_updates_per_pair"]) == 8,
        "checkpoint_summary_hash": str(training["selected_checkpoint_sha256"])
        == str(run["expected_pairmlp_checkpoint_sha256"]),
        "initial_decoder": str(payload["initial_decoder_sha256"])
        == str(training["initial_decoder_sha256"]),
        "current_decoder": str(payload["current_decoder_sha256"])
        == str(run["expected_pairmlp_decoder_sha256"]),
        "loaded_decoder": module_state_sha256(decoder)
        == str(run["expected_pairmlp_decoder_sha256"]),
        "loaded_model": module_state_sha256(model)
        == str(run["expected_pairmlp_model_sha256"]),
        "optimizer_present": bool(payload.get("optimizer_state_dict")),
        "rng_present": all(
            key in payload
            for key in (
                "python_random_state",
                "torch_rng_state",
                "cuda_rng_state",
            )
        ),
        "student_prompt_raw_absent": not bool(
            training["student_prompt_contains_raw_transition"]
        ),
        "qwen_frozen": not bool(training["qwen_parameters_trainable"]),
    }
    if not all(checks.values()):
        raise ValueError(f"Frozen PairMLP checkpoint contract failed: {checks}")
    return checks


def _load_latents(
    *,
    paths: Mapping[str, Path],
    direct: Mapping[str, Any],
    run: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    state_cache = torch.load(paths["state_cache"], map_location="cpu", weights_only=False)
    transition_cache = torch.load(
        paths["transition_cache"], map_location="cpu", weights_only=False
    )
    training = _json(paths["training_summary"])
    model, decoder, payload = _load_pairmlp(
        checkpoint_path=paths["checkpoint"],
        direct=direct,
        transition_view_names=transition_cache["view_names"],
    )
    checkpoint_checks = _validate_checkpoint(
        payload=payload,
        model=model,
        decoder=decoder,
        run=run,
        training=training,
    )
    state_values = state_cache["representations"]["final_layer"].to(torch.float32)
    transition_values = transition_cache["representations"]["final_layer"].to(
        torch.float32
    )
    state_position = {
        str(value): index for index, value in enumerate(state_cache["ordered_ids"])
    }
    transition_position = {
        str(value): index
        for index, value in enumerate(transition_cache["ordered_ids"])
    }
    latents: list[torch.Tensor] = []
    with torch.no_grad():
        for condition in manifest["conditions"]:
            state_id = str(condition["program_state_id"])
            transition_id = str(condition["program_transition_id"])
            state = state_values[state_position[state_id]].unsqueeze(0)
            transition = transition_values[transition_position[transition_id]].unsqueeze(0)
            latents.append(model(state, transition).squeeze(0).cpu())
    values = torch.stack(latents)
    if list(values.shape) != [int(manifest["condition_count"]), LATENT_DIM]:
        raise ValueError(f"Unexpected PairMLP latent shape: {list(values.shape)}")
    if not bool(torch.isfinite(values).all()):
        raise ValueError("PairMLP one-step latents contain NaN/Inf")
    return {
        "format": "direct_pair_behavior_latents_7dg3_v1",
        "global_seed": GLOBAL_SEED,
        "condition_keys": [
            str(row["condition_key"]) for row in manifest["conditions"]
        ],
        "latents": values,
        "pairmlp_checkpoint": str(paths["checkpoint"]),
        "pairmlp_checkpoint_sha256": sha256_file(paths["checkpoint"]),
        "pairmlp_model_sha256": module_state_sha256(model),
        "pairmlp_decoder_sha256": module_state_sha256(decoder),
        "checkpoint_checks": checkpoint_checks,
        "student_prompt_contains_raw_transition": False,
        "observation_excluded": True,
    }


def _directory_hash(path: Path) -> str:
    values = {
        item.name: sha256_file(item)
        for item in sorted(path.glob("*.json"))
        if item.is_file()
    }
    return canonical_sha256(values)


def _preflight(
    *,
    direct: Mapping[str, Any],
    run: Mapping[str, Any],
    replay: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    paths = _paths(direct, run, artifact_dir)
    required = (
        "checkpoint",
        "training_summary",
        "pairmlp_evaluation",
        "parent_direct_final",
        "parent_direct_attempts",
        "parent_g2_final",
        "parent_g2_attempts",
        "state_cache",
        "transition_cache",
        "selector",
        "selector_conditions",
        "parent_smoke",
        "replay_lineage",
        "corpus_summary",
        "decisions",
        "memories",
        "semantic_module",
        "bridge_script",
    )
    _require_paths(paths, required)
    expected_hashes = {
        "checkpoint": run["expected_pairmlp_checkpoint_sha256"],
        "training_summary": run["expected_pairmlp_training_summary_sha256"],
        "pairmlp_evaluation": run["expected_pairmlp_final_evaluation_sha256"],
        "parent_direct_final": run["expected_parent_direct_final_summary_sha256"],
        "parent_g2_final": run["expected_parent_g2_final_summary_sha256"],
        "state_cache": run["expected_state_cache_sha256"],
        "transition_cache": run["expected_transition_cache_sha256"],
        "selector": run["expected_selector_ensemble_sha256"],
    }
    observed_hashes = {name: sha256_file(paths[name]) for name in expected_hashes}
    if any(observed_hashes[name] != str(value) for name, value in expected_hashes.items()):
        raise ValueError(
            f"Immutable G3 input hash mismatch: observed={observed_hashes}"
        )

    training = _json(paths["training_summary"])
    evaluation = _json(paths["pairmlp_evaluation"])
    replay_lineage = _json(paths["replay_lineage"])
    corpus = _json(paths["corpus_summary"])
    parent_smoke = _json(paths["parent_smoke"])
    integrity_checks = {
        "global_seed": int(run["global_seed"]) == GLOBAL_SEED,
        "pairmlp_parent_gate": bool(evaluation["gate"]["passed"]),
        "pairmlp_parent_passed": bool(evaluation["passed"]),
        "selector_unchanged": bool(evaluation["selector_unchanged"]),
        "selector_hash": str(evaluation["selector_sha256"])
        == str(run["expected_selector_ensemble_sha256"]),
        "structural_lineage": str(corpus["lineage_sha256"])
        == str(run["expected_structural_lineage_sha256"]),
        "replay_lineage": str(replay_lineage["lineage_sha256"])
        == str(run["expected_replay_lineage_sha256"]),
        "replay_validated": bool(replay_lineage["replay_validated"]),
        "parent_smoke": bool(parent_smoke["passed"]),
        "parent_direct_attempt": _successful_attempt(
            paths["parent_direct_attempts"],
            str(run["parent_direct_attempt_id"]),
            str(run["parent_direct_run_uuid"]),
        ),
        "parent_g2_attempt": _successful_attempt(
            paths["parent_g2_attempts"],
            str(run["parent_g2_attempt_id"]),
            str(run["parent_g2_run_uuid"]),
        ),
        "qwen_model": str(replay["causal_audit"]["generation"]["model_name"])
        == str(run["expected_model_name"]),
        "prompt_profile": str(
            replay["causal_audit"]["generation"]["prompt_profile"]
        )
        == str(run["expected_prompt_profile"]),
        "injection_k": int(run["expected_injection_k"]) == K_TOKENS == 4,
        "injection_position": str(run["expected_injection_position"])
        == "last_user_k",
        "selected_u8": int(training["selected_updates_per_pair"]) == 8,
    }
    if not all(integrity_checks.values()):
        raise ValueError(f"EXP-025D-G3 parent integrity failed: {integrity_checks}")

    f3 = _f3_rows(paths["selector_conditions"])
    provenance = {
        "pairmlp_checkpoint_sha256": observed_hashes["checkpoint"],
        "pairmlp_model_sha256": str(run["expected_pairmlp_model_sha256"]),
        "pairmlp_decoder_sha256": str(run["expected_pairmlp_decoder_sha256"]),
        "selector_sha256": observed_hashes["selector"],
        "state_cache_sha256": observed_hashes["state_cache"],
        "transition_cache_sha256": observed_hashes["transition_cache"],
        "structural_lineage_sha256": str(corpus["lineage_sha256"]),
        "replay_lineage_sha256": str(replay_lineage["lineage_sha256"]),
    }
    manifest = build_pair_behavior_manifest(
        f3,
        seed=GLOBAL_SEED,
        program_provenance=provenance,
    )
    expected_states = int(run["one_step"]["audit_states"])
    expected_conditions = int(run["one_step"]["total_conditions"])
    primary_states = {
        str(row["state_example_id"])
        for row in manifest["conditions"]
        if str(row["audit_stratum"]) in {"A", "B"}
    }
    manifest_checks = {
        "state_count": int(manifest["state_count"]) == expected_states == 45,
        "condition_count": int(manifest["condition_count"])
        == expected_conditions
        == 135,
        "primary_state_count": len(primary_states)
        == int(run["one_step"]["primary_states"])
        == 32,
        "condition_names": set(manifest["condition_name_counts"])
        == set(PAIR_BEHAVIOR_CONDITIONS),
        "each_condition_45": set(manifest["condition_name_counts"].values())
        == {45},
        "raw_transition_absent": int(manifest["raw_transition_prompt_count"]) == 0,
        "transition_shuffle_changes": all(
            str(row["program_transition_id"])
            != str(row["selector_transition_id"])
            for row in manifest["conditions"]
            if str(row["condition_name"])
            == "P2_pairmlp_shuffled_transition"
        ),
        "state_shuffle_changes": all(
            str(row["program_state_id"]) != str(row["state_example_id"])
            for row in manifest["conditions"]
            if str(row["condition_name"]) == "P3_pairmlp_shuffled_state"
        ),
    }
    if not all(manifest_checks.values()):
        raise ValueError(f"PairBehavior manifest failed: {manifest_checks}")

    paths["manifest"].parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(paths["manifest"], manifest)
    latents = _load_latents(
        paths=paths,
        direct=direct,
        run=run,
        manifest=manifest,
    )
    atomic_torch_save(latents, paths["latents"])
    replay_rates = replay["causal_audit"]["runtime"][
        "replay_seconds_per_condition"
    ]
    runtime = runtime_projection(
        condition_count=expected_conditions,
        generation_rates=run["runtime"]["rates"],
        replay_rates=replay_rates,
        projected_bytes_per_condition=int(
            run["runtime"]["projected_bytes_per_condition"]
        ),
    )
    report = {
        "format": "direct_pair_behavior_preflight_7dg3_v1",
        "run_uuid": str(run["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "immutable_hashes": observed_hashes,
        "integrity_checks": integrity_checks,
        "manifest_checks": manifest_checks,
        "state_count": expected_states,
        "primary_state_count": len(primary_states),
        "condition_count": expected_conditions,
        "qwen_generation_count": expected_conditions,
        "appworld_reconstruction_execution_count": expected_conditions,
        "runtime": runtime,
        "parent_c0_output_set_sha256": _directory_hash(
            paths["parent_c0_outputs"]
        ),
        "parent_f3_output_set_sha256": _directory_hash(
            paths["parent_f3_outputs"]
        ),
        "manifest_sha256": sha256_file(paths["manifest"]),
        "latents_sha256": sha256_file(paths["latents"]),
        "automatic_launch_allowed": True,
        "training_performed": False,
        "passed": True,
    }
    atomic_write_json(paths["preflight"], report)
    atomic_write_text(
        artifact_dir / "runtime_preflight.md",
        "\n".join(
            [
                "# EXP-025D-G3 runtime preflight",
                "",
                f"- global seed: `{GLOBAL_SEED}`",
                f"- audit states: `{expected_states}`",
                f"- P1/P2/P3 generations: `{expected_conditions}`",
                f"- expected H100 hours: `{runtime['scenarios']['expected']['h100_hours']:.4f}`",
                f"- expected wall hours: `{runtime['scenarios']['expected']['wall_hours']:.4f}`",
                "- training or calibration: `none`",
                "",
            ]
        ),
    )
    return report


def _formal(
    *,
    direct: Mapping[str, Any],
    run: Mapping[str, Any],
    replay: Mapping[str, Any],
    artifact_dir: Path,
    attempt: AttemptLedger,
    attempt_id: str,
) -> dict[str, Any]:
    paths = _paths(direct, run, artifact_dir)
    _require_paths(paths, ("preflight", "manifest", "latents"))
    preflight = _json(paths["preflight"])
    if not bool(preflight["passed"] and preflight["automatic_launch_allowed"]):
        raise RuntimeError("PairBehavior preflight did not authorize generation")
    if sha256_file(paths["checkpoint"]) != str(
        run["expected_pairmlp_checkpoint_sha256"]
    ):
        raise ValueError("Frozen PairMLP checkpoint changed after preflight")
    manifest = _json(paths["manifest"])
    latent_payload = torch.load(paths["latents"], map_location="cpu", weights_only=False)
    conditions = list(manifest["conditions"])
    if latent_payload["condition_keys"] != [
        str(row["condition_key"]) for row in conditions
    ]:
        raise ValueError("PairMLP latent order differs from frozen condition manifest")

    generation = replay["causal_audit"]["generation"]
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
        raise RuntimeError("Frozen Qwen contract failed")
    injector, decoder, decoder_sha256 = _build_injector(
        backend=backend,
        decoder_path=paths["checkpoint"],
    )
    if decoder_sha256 != str(run["expected_pairmlp_decoder_sha256"]):
        raise ValueError("Live PairMLP private decoder hash changed")

    examples = _examples_by_state(load_decision_examples(paths["decisions"]))
    records = _records_by_task(load_memory_records(paths["memories"]))
    position = {
        key: index for index, key in enumerate(latent_payload["condition_keys"])
    }
    output_dir = artifact_dir / ROOT_NAME / "condition_outputs"
    completed: list[dict[str, Any]] = []
    resumed = 0
    started = time.perf_counter()
    for ordinal, condition in enumerate(conditions, start=1):
        key = str(condition["condition_key"])
        row, reused = _run_condition(
            condition=condition,
            z=latent_payload["latents"][position[key]],
            output_path=output_dir / condition_checkpoint_name(key),
            stderr_path=(
                artifact_dir / ROOT_NAME / f"worker_logs/formal/{key}.stderr.log"
            ),
            ordinal=ordinal,
            attempt_id=attempt_id,
            settings=direct,
            replay=replay,
            manifest=manifest,
            example=examples[str(condition["state_example_id"])],
            record=records[str(condition["state_task_id"])],
            backend=backend,
            injector=injector,
            decoder=decoder,
            decoder_sha256=decoder_sha256,
            semantic_path=paths["semantic_module"],
            bridge_script=paths["bridge_script"],
        )
        completed.append(row)
        resumed += int(reused)
        attempt.progress(
            status="pairmlp_one_step_formal",
            completed_conditions=len(completed),
            total_conditions=len(conditions),
            latest_validated_checkpoint=str(
                output_dir / condition_checkpoint_name(key)
            ),
        )
        print(
            f"pair behavior {len(completed)}/{len(conditions)} "
            f"{condition['condition_name']}",
            flush=True,
        )
    summary = {
        "format": "direct_pair_behavior_generation_summary_7dg3_v1",
        "global_seed": GLOBAL_SEED,
        "condition_count": len(completed),
        "unique_condition_count": len(
            {str(row["condition_key"]) for row in completed}
        ),
        "resumed_condition_count": resumed,
        "new_generation_count": len(completed) - resumed,
        "qwen_generation_seconds": sum(
            float(row["generation_elapsed_seconds"]) for row in completed
        ),
        "elapsed_seconds": time.perf_counter() - started,
        "same_world_count": sum(
            bool(row["live_worker"]["same_world_execution"]) for row in completed
        ),
        "same_namespace_count": sum(
            bool(row["live_worker"]["same_python_namespace"]) for row in completed
        ),
        "infrastructure_exception_count": sum(
            not bool(row["live_worker"]["same_world_execution"])
            for row in completed
        ),
        "execution_exception_count": sum(
            row["live_worker"]["execution_exception"] is not None
            for row in completed
        ),
        "pairmlp_checkpoint_sha256": sha256_file(paths["checkpoint"]),
        "pairmlp_decoder_sha256": decoder_sha256,
        "qwen_frozen": True,
        "training_performed": False,
        "passed": len(completed) == len(conditions) == 135
        and len({str(row["condition_key"]) for row in completed}) == 135
        and all(row["live_worker"]["same_world_execution"] for row in completed)
        and all(row["live_worker"]["same_python_namespace"] for row in completed),
    }
    atomic_write_json(paths["generation"], summary)
    return summary


def _task_positive_count(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[int, dict[str, Any]]:
    grouped: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        grouped[str(row["state_task_id"])][str(row["condition_name"])].append(row)
    report: dict[str, Any] = {}
    for task_id, values in sorted(grouped.items()):
        p1 = {
            str(row["state_example_id"]): row
            for row in values.get("P1_pairmlp_correct", [])
        }
        c0 = {
            str(row["state_example_id"]): row
            for row in values.get("C0_bare", [])
        }
        shared = sorted(set(p1) & set(c0))
        if not shared:
            continue
        signature = statistics.fmean(
            float(p1[key]["metrics"]["canonical_procedural_signature_match"])
            - float(c0[key]["metrics"]["canonical_procedural_signature_match"])
            for key in shared
        )
        successor = statistics.fmean(
            float(p1[key]["metrics"]["semantic_successor_match"])
            - float(c0[key]["metrics"]["semantic_successor_match"])
            for key in shared
        )
        report[task_id] = {
            "paired_state_count": len(shared),
            "action_signature_difference": signature,
            "semantic_successor_difference": successor,
            "positive_relative_behavior": signature > 0.0 or successor > 0.0,
        }
    return (
        sum(bool(row["positive_relative_behavior"]) for row in report.values()),
        report,
    )


def _analyze(
    *,
    run: Mapping[str, Any],
    direct: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    paths = _paths(direct, run, artifact_dir)
    generation = _json(paths["generation"])
    if not bool(generation["passed"]):
        raise RuntimeError("PairBehavior one-step infrastructure is invalid")
    output_dir = artifact_dir / ROOT_NAME / "condition_outputs"
    p_rows = [_json(path) for path in sorted(output_dir.glob("*.json"))]
    if len(p_rows) != 135:
        raise ValueError(f"Expected 135 P1/P2/P3 rows, found {len(p_rows)}")
    c0 = _load_parent_rows(paths["parent_c0_outputs"], "C0_bare")
    f3 = _load_parent_rows(paths["parent_f3_outputs"], "F3_deployment_e_field_raw")
    combined = p_rows + c0 + f3
    primary = [row for row in combined if str(row["audit_stratum"]) in {"A", "B"}]
    primary_states = {str(row["state_example_id"]) for row in primary}
    if len(primary_states) != 32:
        raise ValueError(f"Expected 32 primary states, found {len(primary_states)}")
    pairs = (
        ("P1_pairmlp_correct", "C0_bare"),
        ("P1_pairmlp_correct", "F3_deployment_e_field_raw"),
        ("P1_pairmlp_correct", "P2_pairmlp_shuffled_transition"),
        ("P1_pairmlp_correct", "P3_pairmlp_shuffled_state"),
    )
    comparisons = {
        f"{left}_minus_{right}": comparison_set(
            primary,
            left=left,
            right=right,
            bootstrap_samples=int(run["bootstrap_samples"]),
            seed=GLOBAL_SEED,
            per_metric_seed_offset=False,
        )
        for left, right in pairs
    }
    f3_c0 = comparison_set(
        primary,
        left="F3_deployment_e_field_raw",
        right="C0_bare",
        bootstrap_samples=int(run["bootstrap_samples"]),
        seed=GLOBAL_SEED,
        per_metric_seed_offset=False,
    )
    positive_count, task_deltas = _task_positive_count(primary)
    gate = pair_behavior_gate(
        p1_minus_c0=comparisons["P1_pairmlp_correct_minus_C0_bare"],
        p1_minus_p2=comparisons[
            "P1_pairmlp_correct_minus_P2_pairmlp_shuffled_transition"
        ],
        p1_minus_p3=comparisons[
            "P1_pairmlp_correct_minus_P3_pairmlp_shuffled_state"
        ],
        f3_minus_c0=f3_c0,
        positive_task_count=positive_count,
        material_degradation_tolerance=float(
            run["one_step"]["material_degradation_tolerance"]
        ),
    )
    summary = {
        "format": "direct_pair_behavior_analysis_7dg3_v1",
        "run_uuid": str(run["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "condition_metrics_all": condition_summary(combined),
        "condition_metrics_primary": condition_summary(primary),
        "per_task": per_task_summary(combined),
        "comparisons_primary": comparisons,
        "f3_raw_minus_c0": f3_c0,
        "positive_task_count": positive_count,
        "positive_task_details": task_deltas,
        "gate": gate,
        "decision_branch": gate["decision_branch"],
        "pairmlp_one_step_behavior_passed": bool(gate["passed"]),
        "factorized_or_policy_training_started": False,
        "actual_qwen_h100_hours": float(generation["qwen_generation_seconds"])
        / 3600.0,
        "actual_wall_hours": float(generation["elapsed_seconds"]) / 3600.0,
    }
    atomic_write_json(paths["analysis"], summary)
    metrics = summary["condition_metrics_primary"]
    lines = [
        "# EXP-025D-G3 Direct PairMLP one-step behavioral audit",
        "",
        f"- run UUID: `{run['run_uuid']}`",
        f"- global seed: `{GLOBAL_SEED}`",
        f"- frozen checkpoint: `{run['expected_pairmlp_checkpoint_sha256']}`",
        "- formal conditions: `135/135`",
        "- primary states: `32`",
        f"- positive tasks: `{positive_count}/9`",
        f"- decision branch: `{gate['decision_branch']}`",
        "",
        "## Primary condition means",
        "",
    ]
    for name in (
        "C0_bare",
        "F3_deployment_e_field_raw",
        "P1_pairmlp_correct",
        "P2_pairmlp_shuffled_transition",
        "P3_pairmlp_shuffled_state",
    ):
        values = metrics[name]["metrics"]
        lines.append(
            f"- {name}: API={values['exact_primary_app_api_match']:.4f}, "
            f"signature={values['canonical_procedural_signature_match']:.4f}, "
            f"execution={values['execution_success']:.4f}, "
            f"successor={values['semantic_successor_match']:.4f}, "
            f"observation={values['normalized_observation_similarity']:.4f}"
        )
    lines.extend(("", "## Gate", ""))
    for name, value in gate["checks"].items():
        lines.append(f"- {name}: `{str(bool(value)).lower()}`")
    lines.append("")
    atomic_write_text(artifact_dir / "pair_behavior_report.md", "\n".join(lines))
    return summary


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    replay_cfg = load_config(args.replay_config)
    direct = cfg.raw["stage_c_7dg"]
    run = cfg.raw["stage_c_7dg3"]
    replay = replay_cfg.raw["stage_c_7b"]
    require_global_seed(int(run["global_seed"]))
    seed_everything(GLOBAL_SEED)
    if os.name != "nt" and not os.path.ismount(Path(str(run["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    paths = _paths(direct, run, args.artifact_dir)
    data_hashes = {
        name: sha256_file(path)
        for name, path in {
            "config": args.config,
            "replay_config": args.replay_config,
            "pairmlp_checkpoint": paths["checkpoint"],
            "selector": paths["selector"],
            "state_cache": paths["state_cache"],
            "transition_cache": paths["transition_cache"],
        }.items()
        if path.exists()
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(run["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"direct_pair_behavior_{args.phase}",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(run["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "preflight":
            result = _preflight(
                direct=direct,
                run=run,
                replay=replay,
                artifact_dir=args.artifact_dir,
            )
        elif args.phase == "formal":
            result = _formal(
                direct=direct,
                run=run,
                replay=replay,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
                attempt_id=args.attempt_id,
            )
        else:
            result = _analyze(
                run=run,
                direct=direct,
                artifact_dir=args.artifact_dir,
            )
        checkpoints = {
            "preflight": paths["preflight"],
            "formal": paths["generation"],
            "analyze": paths["analysis"],
        }
        attempt.progress(
            status=f"direct_pair_behavior_{args.phase}_completed",
            latest_validated_checkpoint=str(checkpoints[args.phase]),
            result_passed=bool(
                result.get("passed", result.get("gate", {}).get("passed", True))
            ),
        )
        print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
