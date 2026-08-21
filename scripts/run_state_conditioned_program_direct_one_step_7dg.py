from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.model.backends.hf_qwen import HFQwenBackend
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.procedural_causal_analysis_7b import (
    comparison_set,
    condition_summary,
    per_task_summary,
)
from rcmf.training.state_conditioned_program_direct_7dg import (
    GLOBAL_SEED,
    require_global_seed,
    seed_everything,
)
from rcmf.training.state_conditioned_program_fast_7df import (
    build_compiled_one_step_manifest,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.training.datasets import load_decision_examples, load_memory_records
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
    FACTORIZED_NAME,
    _model,
)
from scripts.run_state_conditioned_program_fast_7df import LATENT_DIM
from scripts.run_state_conditioned_program_fast_one_step_7df import (
    _build_injector,
    _f3_rows,
    _load_parent_rows,
    _positive_task_count,
    _run_condition,
)
from rcmf.training.procedural_causal_audit_7b import condition_checkpoint_name


ONE_STEP_ROOT = "one_step"
PRIMARY_METRICS = (
    "exact_primary_app_api_match",
    "canonical_procedural_signature_match",
    "execution_success",
    "semantic_successor_match",
)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_state_conditioned_program_direct_7dg.yaml"
        ),
    )
    parser.add_argument(
        "--replay-config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=("preflight", "smoke", "formal", "analyze"), required=True
    )
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--tmux-session", default="exp025dg")
    return parser.parse_args()


def _paths(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Path]:
    parent_b = Path(str(settings["parent_exp025b"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    parent_cr = Path(str(settings["parent_exp025cr"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    return {
        "direct_summary": artifact_dir / "direct_behavior_summary.json",
        "factor_training": artifact_dir / "factorized/training_summary.json",
        "state_cache": parent_c
        / "representation_cache/multiview/state_multiview.pt",
        "transition_cache": parent_c
        / "representation_cache/multiview/transition_multiview.pt",
        "selector": parent_c / "selector/ensemble_scores.pt",
        "selector_conditions": parent_cr / "selector_condition_manifest.json",
        "parent_c0_outputs": parent_b / "condition_outputs",
        "parent_f3_outputs": parent_cr / "selector_condition_outputs",
        "parent_smoke": parent_b / "lifecycle_smoke/smoke_summary.json",
        "decisions": corpus / "decision_examples.jsonl",
        "memories": corpus / "memory_records.jsonl",
        "semantic_module": Path("rcmf/training/appworld_replay_clean_rebuild_7b.py"),
        "bridge_script": Path("scripts/appworld_live_one_step_bridge_7b.py"),
    }


def _require_paths(paths: Mapping[str, Path], names: Sequence[str]) -> None:
    missing = {name: str(paths[name]) for name in names if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing direct one-step inputs: {missing}")


def _selected_factor_checkpoint(paths: Mapping[str, Path]) -> Path:
    summary = _json(paths["factor_training"])
    checkpoint = Path(str(summary["selected_checkpoint"]))
    if not checkpoint.exists():
        raise FileNotFoundError(checkpoint)
    if int(summary["global_seed"]) != GLOBAL_SEED:
        raise ValueError("Factorized checkpoint does not use the locked global seed")
    return checkpoint


def _load_program_latents(
    *,
    settings: Mapping[str, Any],
    artifact_dir: Path,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    paths = _paths(settings, artifact_dir)
    state_cache = torch.load(
        paths["state_cache"], map_location="cpu", weights_only=False
    )
    transition_cache = torch.load(
        paths["transition_cache"], map_location="cpu", weights_only=False
    )
    model = _model(
        FACTORIZED_NAME,
        settings=settings,
        transition_view_names=transition_cache["view_names"],
        device=torch.device("cpu"),
    )
    checkpoint = _selected_factor_checkpoint(paths)
    training_summary = _json(paths["factor_training"])
    program_gain = float(training_summary.get("selected_gamma", 1.0))
    payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    model.load_state_dict(payload["model_state_dict"])
    model.eval()
    states = state_cache["representations"]["final_layer"].to(torch.float32)
    transitions = transition_cache["representations"]["final_layer"].to(
        torch.float32
    )
    state_position = {
        str(value): index for index, value in enumerate(state_cache["ordered_ids"])
    }
    transition_position = {
        str(value): index
        for index, value in enumerate(transition_cache["ordered_ids"])
    }
    output = []
    with torch.no_grad():
        for condition in manifest["conditions"]:
            state_id = str(condition["state_example_id"])
            transition_id = str(condition["program_transition_id"])
            state = states[state_position[state_id]].unsqueeze(0)
            transition = transitions[transition_position[transition_id]].unsqueeze(0)
            name = str(condition["condition_name"])
            if name == "H4_zero_program":
                z = torch.zeros(1, LATENT_DIM)
            else:
                components = model.components(state, transition)
                z = (
                    components["static"]
                    if name == "H2_compiled_static_only"
                    else components["z"]
                )
                z = z * program_gain
            output.append(z.squeeze(0).cpu())
    latents = torch.stack(output)
    if not bool(torch.isfinite(latents).all()):
        raise ValueError("Direct compiled one-step latents contain nonfinite values")
    return {
        "format": "compiled_program_one_step_latents_7dg_v1",
        "global_seed": GLOBAL_SEED,
        "condition_keys": [
            str(row["condition_key"]) for row in manifest["conditions"]
        ],
        "latents": latents,
        "factorized_checkpoint": str(checkpoint),
        "factorized_checkpoint_sha256": sha256_file(checkpoint),
        "model_sha256": str(_json(paths["factor_training"])["model_sha256"]),
        "decoder_sha256": str(
            _json(paths["factor_training"])["trained_decoder_sha256"]
        ),
        "program_gain": program_gain,
        "student_prompt_contains_raw_transition": False,
    }


def _preflight(
    *,
    settings: Mapping[str, Any],
    replay: Mapping[str, Any],
    artifact_dir: Path,
    run_settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    run_settings = settings if run_settings is None else run_settings
    paths = _paths(settings, artifact_dir)
    _require_paths(
        paths,
        (
            "direct_summary",
            "factor_training",
            "state_cache",
            "transition_cache",
            "selector",
            "selector_conditions",
            "parent_smoke",
        ),
    )
    direct = _json(paths["direct_summary"])
    if not bool(direct.get("teacher_forced_factorized_passed")):
        raise RuntimeError("Factorized teacher-forced gate did not authorize one-step")
    if sha256_file(paths["selector"]) != str(
        settings["expected_selector_ensemble_sha256"]
    ):
        raise ValueError("Frozen selector hash changed")
    f3 = _f3_rows(paths["selector_conditions"])
    manifest = build_compiled_one_step_manifest(f3, seed=GLOBAL_SEED)
    if int(manifest["state_count"]) != int(settings["one_step"]["audit_states"]):
        raise ValueError("Direct one-step state count differs")
    root = artifact_dir / ONE_STEP_ROOT
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(root / "condition_manifest.json", manifest)
    latents = _load_program_latents(
        settings=settings, artifact_dir=artifact_dir, manifest=manifest
    )
    atomic_torch_save(latents, root / "program_latents.pt")
    if not bool(_json(paths["parent_smoke"]).get("passed")):
        raise RuntimeError("Parent live bridge lifecycle smoke is invalid")
    condition_count = int(manifest["condition_count"])
    replay_rates = replay["causal_audit"]["runtime"]
    direct_h100 = float(direct["elapsed_h100_hours"])
    scenarios = {}
    for name in ("best", "expected", "conservative"):
        h100_seconds = condition_count * float(
            run_settings["runtime"]["rates"][name]["generation"]
        )
        wall_seconds = condition_count * (
            float(run_settings["runtime"]["rates"][name]["generation"])
            + float(replay_rates["replay_seconds_per_condition"][name])
        )
        scenarios[name] = {
            "one_step_h100_hours": h100_seconds / 3600.0,
            "one_step_wall_hours": wall_seconds / 3600.0,
            "cumulative_h100_hours": direct_h100 + h100_seconds / 3600.0,
        }
    review_threshold = float(
        run_settings.get(
            "review_threshold_h100_hours",
            run_settings["runtime"].get("review_threshold_h100_hours", 18.0),
        )
    )
    extension_runtime = artifact_dir / "runtime_preflight.json"
    launch_allowed = scenarios["expected"]["cumulative_h100_hours"] <= review_threshold
    if run_settings is not settings and extension_runtime.exists():
        launch_allowed = bool(_json(extension_runtime)["automatic_launch_allowed"])
    report = {
        "format": "compiled_program_one_step_preflight_7dg_v1",
        "global_seed": GLOBAL_SEED,
        "state_count": int(manifest["state_count"]),
        "condition_count": condition_count,
        "qwen_generation_count": condition_count,
        "appworld_reconstruction_execution_count": condition_count,
        "runtime": scenarios,
        "review_threshold_h100_hours": review_threshold,
        "automatic_launch_allowed": launch_allowed,
        "authorization_source": (
            str(extension_runtime)
            if run_settings is not settings
            else "one_step_cumulative_projection"
        ),
        "projected_artifact_bytes": condition_count * 2_359_296,
        "parent_lifecycle_smoke_reused": True,
        "selector_sha256": sha256_file(paths["selector"]),
        "factorized_checkpoint_sha256": latents[
            "factorized_checkpoint_sha256"
        ],
        "manifest_sha256": manifest["manifest_sha256"],
        "latents_sha256": sha256_file(root / "program_latents.pt"),
        "passed": True,
    }
    atomic_write_json(root / "preflight.json", report)
    return report


def _program_phase(
    *,
    phase: str,
    settings: Mapping[str, Any],
    replay: Mapping[str, Any],
    artifact_dir: Path,
    attempt: AttemptLedger,
    attempt_id: str,
) -> dict[str, Any]:
    root = artifact_dir / ONE_STEP_ROOT
    preflight = _json(root / "preflight.json")
    if not bool(preflight["automatic_launch_allowed"]):
        raise RuntimeError("Direct one-step work exceeds the 18-hour review threshold")
    manifest = _json(root / "condition_manifest.json")
    latent_payload = torch.load(
        root / "program_latents.pt", map_location="cpu", weights_only=False
    )
    conditions = list(manifest["conditions"])
    if latent_payload["condition_keys"] != [
        str(row["condition_key"]) for row in conditions
    ]:
        raise ValueError("Direct latent ordering differs from the manifest")
    if phase == "smoke":
        by_state: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in conditions:
            by_state[str(row["state_example_id"])].append(row)
        conditions = [
            row
            for state_id in sorted(by_state)[:2]
            for row in by_state[state_id]
            if str(row["condition_name"])
            in {"H1_compiled_full_factorized", "H4_zero_program"}
        ]
    elif phase != "formal":
        raise ValueError(f"Unknown one-step generation phase: {phase}")
    paths = _paths(settings, artifact_dir)
    _require_paths(
        paths,
        (
            "decisions",
            "memories",
            "semantic_module",
            "bridge_script",
            "factor_training",
        ),
    )
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
    checkpoint = _selected_factor_checkpoint(paths)
    injector, decoder, decoder_sha256 = _build_injector(
        backend=backend, decoder_path=checkpoint
    )
    examples = _examples_by_state(load_decision_examples(paths["decisions"]))
    records = _records_by_task(load_memory_records(paths["memories"]))
    position = {
        key: index for index, key in enumerate(latent_payload["condition_keys"])
    }
    output_dir = root / (
        "lifecycle_smoke/condition_outputs"
        if phase == "smoke"
        else "condition_outputs"
    )
    completed = []
    resumed = 0
    started = time.perf_counter()
    for ordinal, condition in enumerate(conditions, start=1):
        key = str(condition["condition_key"])
        row, reused = _run_condition(
            condition=condition,
            z=latent_payload["latents"][position[key]],
            output_path=output_dir / condition_checkpoint_name(key),
            stderr_path=root / f"worker_logs/{phase}/{key}.stderr.log",
            ordinal=ordinal,
            attempt_id=attempt_id,
            settings=settings,
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
            status=f"direct_compiled_one_step_{phase}",
            completed_conditions=len(completed),
            total_conditions=len(conditions),
            latest_validated_checkpoint=str(
                output_dir / condition_checkpoint_name(key)
            ),
        )
        print(
            f"direct one-step {phase} {len(completed)}/{len(conditions)} "
            f"{condition['condition_name']}",
            flush=True,
        )
    summary = {
        "format": f"compiled_program_one_step_{phase}_summary_7dg_v1",
        "global_seed": GLOBAL_SEED,
        "phase": phase,
        "condition_count": len(completed),
        "unique_condition_count": len({row["condition_key"] for row in completed}),
        "resumed_condition_count": resumed,
        "qwen_generation_seconds": sum(
            float(row["generation_elapsed_seconds"]) for row in completed
        ),
        "elapsed_seconds": time.perf_counter() - started,
        "same_world_count": sum(
            bool(row["live_worker"]["same_world_execution"]) for row in completed
        ),
        "exception_count": sum(
            row["live_worker"]["execution_exception"] is not None for row in completed
        ),
        "passed": len(completed) == len(conditions)
        and len({row["condition_key"] for row in completed}) == len(conditions)
        and all(row["live_worker"]["same_world_execution"] for row in completed),
    }
    path = (
        root / "lifecycle_smoke/smoke_summary.json"
        if phase == "smoke"
        else root / "generation_summary.json"
    )
    atomic_write_json(path, summary)
    return summary


def _analyze(
    *,
    settings: Mapping[str, Any],
    artifact_dir: Path,
    success_branch: str = "compiled_transition_program_direct_pilot_passed",
    failure_branch: str = "compiled_program_not_behaviorally_retained",
) -> dict[str, Any]:
    root = artifact_dir / ONE_STEP_ROOT
    generation = _json(root / "generation_summary.json")
    if not bool(generation["passed"]):
        raise RuntimeError("clean_corpus_behavioral_audit_infrastructure_invalid")
    paths = _paths(settings, artifact_dir)
    h_rows = [
        _json(path) for path in sorted((root / "condition_outputs").glob("*.json"))
    ]
    if len(h_rows) != 180:
        raise ValueError(f"Expected 180 direct compiled rows, found {len(h_rows)}")
    c0 = _load_parent_rows(paths["parent_c0_outputs"], "C0_bare")
    f3 = _load_parent_rows(
        paths["parent_f3_outputs"], "F3_deployment_e_field_raw"
    )
    combined = h_rows + c0 + f3
    primary = [row for row in combined if str(row["audit_stratum"]) in {"A", "B"}]
    pairs = (
        ("H1_compiled_full_factorized", "C0_bare"),
        ("H1_compiled_full_factorized", "F3_deployment_e_field_raw"),
        ("H1_compiled_full_factorized", "H2_compiled_static_only"),
        ("H1_compiled_full_factorized", "H3_compiled_shuffled_transition"),
        ("H1_compiled_full_factorized", "H4_zero_program"),
    )
    comparisons = {
        f"{left}_minus_{right}": comparison_set(
            primary,
            left=left,
            right=right,
            bootstrap_samples=int(settings["bootstrap_samples"]),
            seed=GLOBAL_SEED,
        )
        for left, right in pairs
    }
    h1_c0 = comparisons["H1_compiled_full_factorized_minus_C0_bare"]
    h1_h2 = comparisons[
        "H1_compiled_full_factorized_minus_H2_compiled_static_only"
    ]
    h1_h3 = comparisons[
        "H1_compiled_full_factorized_minus_H3_compiled_shuffled_transition"
    ]
    f3_c0 = comparison_set(
        primary,
        left="F3_deployment_e_field_raw",
        right="C0_bare",
        bootstrap_samples=int(settings["bootstrap_samples"]),
        seed=GLOBAL_SEED,
    )
    retention = {}
    for metric in (
        "canonical_procedural_signature_match",
        "semantic_successor_match",
    ):
        denominator = float(f3_c0[metric]["difference"])
        numerator = float(h1_c0[metric]["difference"])
        retention[metric] = (
            None if abs(denominator) <= 1.0e-12 else numerator / denominator
        )
    positive_tasks = _positive_task_count(primary)
    checks = {
        "improves_signature_or_successor": any(
            float(h1_c0[name]["difference"]) > 0.0
            for name in (
                "canonical_procedural_signature_match",
                "semantic_successor_match",
            )
        ),
        "retains_40_percent_one_metric": any(
            value is not None and value >= 0.40 for value in retention.values()
        ),
        "beats_static": any(
            float(h1_h2[name]["difference"]) > 0.0
            for name in (
                "canonical_procedural_signature_match",
                "semantic_successor_match",
            )
        ),
        "beats_shuffled": any(
            float(h1_h3[name]["difference"]) > 0.0
            for name in (
                "canonical_procedural_signature_match",
                "semantic_successor_match",
            )
        ),
        "execution_drop_lte_0_05": float(
            h1_c0["execution_success"]["difference"]
        )
        >= -0.05,
        "positive_at_least_5_of_9_tasks": positive_tasks >= 5,
    }
    passed = all(checks.values())
    decision = (
        success_branch
        if passed
        else failure_branch
    )
    summary = {
        "format": "compiled_program_one_step_analysis_7dg_v1",
        "global_seed": GLOBAL_SEED,
        "condition_metrics_all": condition_summary(combined),
        "condition_metrics_primary": condition_summary(primary),
        "per_task": per_task_summary(combined),
        "comparisons_primary": comparisons,
        "f3_raw_minus_c0": f3_c0,
        "oracle_gain_retention": retention,
        "positive_task_count": positive_tasks,
        "gate": {"checks": checks, "passed": passed},
        "decision_branch": decision,
        "compiled_program_works": passed,
        "program_training_remains_blocked": True,
        "actual_qwen_h100_hours": float(generation["qwen_generation_seconds"])
        / 3600.0,
        "actual_wall_hours": float(generation["elapsed_seconds"]) / 3600.0,
    }
    atomic_write_json(root / "analysis.json", summary)
    atomic_write_text(
        root / "one_step_report.md",
        "\n".join(
            [
                "# EXP-025D-Direct compiled one-step audit",
                "",
                f"- global seed: `{GLOBAL_SEED}`",
                f"- conditions: `{len(h_rows)}`",
                f"- primary states: `{len({row['state_example_id'] for row in primary})}`",
                f"- positive tasks: `{positive_tasks}/9`",
                f"- decision branch: `{decision}`",
                "",
            ]
        ),
    )
    return summary


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    replay_cfg = load_config(args.replay_config)
    settings = cfg.raw["stage_c_7dg"]
    run_settings = cfg.raw.get("stage_c_7dg2", settings)
    replay = replay_cfg.raw["stage_c_7b"]
    extension = "stage_c_7dg2" in cfg.raw
    require_global_seed(int(settings["global_seed"]))
    seed_everything(GLOBAL_SEED)
    if os.name != "nt" and not os.path.ismount(
        Path(str(settings["persistent_root"]))
    ):
        raise RuntimeError("Persistent filesystem is not mounted")
    paths = _paths(settings, args.artifact_dir)
    data_hashes = {
        name: sha256_file(path)
        for name, path in {
            "config": args.config,
            "replay_config": args.replay_config,
            "selector": paths["selector"],
            "direct_summary": paths["direct_summary"],
        }.items()
        if path.exists()
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(run_settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"compiled_program_direct_one_step_{args.phase}",
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
        heartbeat_interval_s=float(run_settings["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "preflight":
            result = _preflight(
                settings=settings,
                replay=replay,
                artifact_dir=args.artifact_dir,
                run_settings=run_settings,
            )
        elif args.phase in {"smoke", "formal"}:
            if args.phase == "formal" and not extension:
                smoke = _json(
                    args.artifact_dir
                    / ONE_STEP_ROOT
                    / "lifecycle_smoke/smoke_summary.json"
                )
                if not bool(smoke["passed"]):
                    raise RuntimeError("Direct lifecycle smoke did not pass")
            result = _program_phase(
                phase=args.phase,
                settings=settings,
                replay=replay,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
                attempt_id=args.attempt_id,
            )
        else:
            result = _analyze(
                settings=settings,
                artifact_dir=args.artifact_dir,
                success_branch=(
                    "compiled_transition_program_r16_validated"
                    if extension
                    else "compiled_transition_program_direct_pilot_passed"
                ),
                failure_branch=(
                    "calibrated_factorized_program_not_behaviorally_retained"
                    if extension
                    else "compiled_program_not_behaviorally_retained"
                ),
            )
        checkpoints = {
            "preflight": "preflight.json",
            "smoke": "lifecycle_smoke/smoke_summary.json",
            "formal": "generation_summary.json",
            "analyze": "analysis.json",
        }
        checkpoint = args.artifact_dir / ONE_STEP_ROOT / checkpoints[args.phase]
        attempt.progress(
            status=f"direct_compiled_one_step_{args.phase}_completed",
            latest_validated_checkpoint=str(checkpoint),
            result_passed=bool(
                result.get("passed", result.get("gate", {}).get("passed", True))
            ),
        )
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
