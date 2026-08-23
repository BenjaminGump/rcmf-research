from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
import torch.nn.functional as F

from rcmf.config import load_config
from rcmf.training.appworld_structured_rescue_7hr import (
    GLOBAL_SEED,
    StructuredLatentComposer,
    select_compiler_checkpoint,
    stable_key,
)
from rcmf.training.deep_residual_amortization_7f import (
    differentiable_layer_ratio_projection,
)
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, sha256_file
from scripts.run_appworld_structured_compiler_7hr import (
    CHECKPOINT_FORMAT,
    StaticFeatureBank,
    _json,
    _load_parent,
    _paths,
    _policy_loss,
    _program,
)
from scripts.run_deep_residual_amortized_one_step_7f import _run_condition
from scripts.run_procedural_causal_audit_7b import _examples_by_state, _records_by_task
from scripts.run_state_conditioned_program_direct_7dg import _load_representations


VALIDATION_FORMAT = "appworld_structured_compiler_train_validation_7hr_v1"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_structured_rescue_7hr.yaml"),
    )
    parser.add_argument(
        "--replay-config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("u2", "u4", "select"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp028a_validation")
    return parser.parse_args()


def _control_manifest(rows: Sequence[Mapping[str, Any]], updates: int) -> dict[str, Any]:
    conditions = []
    for row in rows:
        state_id = str(row["state_example_id"])
        transition_id = str(row["selected_transition_id"])
        transition_candidates = [
            other
            for other in rows
            if str(other["selected_transition_id"]) != transition_id
            and str(other["selected_class_id"]) != str(row["selected_class_id"])
        ]
        state_candidates = [
            other
            for other in rows
            if str(other["state_task_id"]) != str(row["state_task_id"])
        ]
        if not transition_candidates or not state_candidates:
            raise RuntimeError("Heldout-train compiler controls are unavailable")
        transition_mismatch = min(
            transition_candidates,
            key=lambda other: stable_key(
                GLOBAL_SEED,
                "7hr-validation-transition",
                state_id,
                other["selected_transition_id"],
            ),
        )
        state_mismatch = min(
            state_candidates,
            key=lambda other: stable_key(
                GLOBAL_SEED,
                "7hr-validation-state",
                state_id,
                other["state_example_id"],
            ),
        )
        definitions = (
            ("V1_structured_correct", state_id, transition_id),
            (
                "V2_transition_shuffle",
                state_id,
                str(transition_mismatch["selected_transition_id"]),
            ),
            (
                "V3_state_shuffle",
                str(state_mismatch["state_example_id"]),
                transition_id,
            ),
            ("V0_zero", state_id, transition_id),
        )
        for name, program_state, program_transition in definitions:
            conditions.append(
                {
                    "condition_key": f"7hr-u{updates:02d}::{state_id}::{name}",
                    "condition_name": name,
                    "prompt_kind": "compiled_program",
                    "state_example_id": state_id,
                    "state_task_id": str(row["state_task_id"]),
                    "state_step_id": int(row["state_step_id"]),
                    "audit_stratum": "heldout_train_task_validation",
                    "transition_id": transition_id,
                    "program_state_example_id": program_state,
                    "program_transition_id": program_transition,
                    "feature_state_example_id": program_state,
                    "feature_transition_id": program_transition,
                    "label": str(row["label"]),
                }
            )
    payload = {
        "format": "appworld_structured_compiler_validation_manifest_7hr_v1",
        "global_seed": GLOBAL_SEED,
        "updates_per_pair": updates,
        "state_count": len(rows),
        "condition_count": len(conditions),
        "conditions": conditions,
        "outcomes_used_for_control_selection": False,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def _load_models(
    *,
    cfg: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    updates: int,
    device: torch.device,
) -> tuple[Any, Any, Any, Mapping[str, Any], Mapping[str, Any]]:
    representations = _load_representations(
        {"state_cache": paths["state_cache"], "transition_cache": paths["transition_cache"]},
        device,
    )
    parent, decoder, _ = _load_parent(
        cfg=cfg, paths=paths, representations=representations, device=device
    )
    checkpoint_path = paths["root"] / f"checkpoints/model_u{updates:02d}.pt"
    checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)
    if str(checkpoint["format"]) != CHECKPOINT_FORMAT:
        raise ValueError("Structured compiler checkpoint format differs")
    gate = torch.load(paths["gate"], map_location="cpu", weights_only=False)
    composer = StructuredLatentComposer(
        len(gate["feature_names"]),
        int(settings["compiler"]["structured_hidden_dim"]),
        int(settings["compiler"]["program_dim"]),
    ).to(device)
    composer.load_state_dict(checkpoint["composer_state_dict"])
    decoder.load_state_dict(checkpoint["decoder_state_dict"])
    composer.eval()
    decoder.eval()
    return parent, composer, decoder, representations, gate


def _compile_deltas(
    *,
    manifest: Mapping[str, Any],
    outcomes: Mapping[str, Mapping[str, Any]],
    bank: StaticFeatureBank,
    parent: Any,
    composer: Any,
    decoder: Any,
    representations: Mapping[str, Any],
    gate: Mapping[str, Any],
    device: torch.device,
) -> tuple[torch.Tensor, list[dict[str, Any]]]:
    state_position = representations["state_position"]
    transition_position = representations["transition_position"]
    values = []
    gate_rows = []
    with torch.no_grad():
        for condition in manifest["conditions"]:
            state_id = str(condition["state_example_id"])
            correct_features = list(outcomes[state_id]["feature_values"])
            gate_result = bank.gate_probabilities(correct_features)
            if condition["condition_name"] == "V0_zero" or not gate_result["gate_on"]:
                delta = torch.zeros(4, 4, 4096)
            else:
                program_state = str(condition["program_state_example_id"])
                program_transition = str(condition["program_transition_id"])
                features = bank.feature(program_state, program_transition)
                feature = torch.tensor([features], dtype=torch.float32, device=device)
                normalized = (
                    feature - gate["standardizer_mean"].to(device)
                ) / gate["standardizer_std"].to(device)
                state = representations["state_values"][state_position[program_state]].unsqueeze(0).to(device)
                transition = representations["transition_values"][transition_position[program_transition]].unsqueeze(0).to(device)
                base_latent = parent(state, transition)
                latent = composer(
                    normalized,
                    base_latent,
                    torch.tensor([gate_result["positive_probability"]], device=device),
                )
                delta = decoder(latent)[0].cpu()
            values.append(delta)
            gate_rows.append(
                {
                    "condition_key": str(condition["condition_key"]),
                    "correct_pair_gate": gate_result,
                }
            )
    return torch.stack(values), gate_rows


def _teacher_forced_kl(
    *,
    rows: Sequence[Mapping[str, Any]],
    deltas: torch.Tensor,
    manifest: Mapping[str, Any],
    teacher: Mapping[str, Any],
    backend: Any,
) -> float:
    delta_by_key = {
        str(condition["condition_key"]): deltas[index]
        for index, condition in enumerate(manifest["conditions"])
    }
    base_by_state = {
        state_id: teacher["base_states"][index].to(torch.float32)
        for index, state_id in enumerate(teacher["ordered_state_ids"])
    }
    values = []
    for row in rows:
        state_id = str(row["state_example_id"])
        key = f"7hr-u{int(manifest['updates_per_pair']):02d}::{state_id}::V1_structured_correct"
        target = "raw" if row["label"] == "POSITIVE" else "bare"
        policy = teacher["policy_rows"][state_id][target]
        target_teacher = teacher["teacher_rows"][state_id][target]
        batch = __import__(
            "scripts.run_stage_c_oracle_capacity_5e", fromlist=["_collate"]
        )._collate([policy], device=backend.device, k=4)
        base = base_by_state[state_id].unsqueeze(0).to(backend.device)
        projected, _ = differentiable_layer_ratio_projection(
            delta_by_key[key].unsqueeze(0).to(backend.device), base, maximum_ratio=1.0
        )
        from scripts.run_deep_residual_carrier_7e import _forward_residual

        with torch.no_grad():
            result = _forward_residual(
                backend=backend,
                batch=batch,
                delta=projected,
                layer_indices=[7, 14, 21, 28],
                original_states=base,
            )
            kl, _ = _policy_loss(result["target_logits"], target_teacher)
        values.append(float(kl.cpu()))
    return statistics.fmean(values)


def _summaries(outputs: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    names = sorted({str(row["condition_name"]) for row in outputs})
    metrics = (
        "canonical_procedural_signature_match",
        "semantic_successor_match",
        "execution_success",
    )
    return {
        name: {
            key: statistics.fmean(
                float(row["metrics"][key])
                for row in outputs
                if row["condition_name"] == name
            )
            for key in metrics
        }
        for name in names
    }


def _validate(
    *,
    cfg: Any,
    replay: Mapping[str, Any],
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    artifact_dir: Path,
    updates: int,
    attempt: AttemptLedger,
    attempt_id: str,
) -> dict[str, Any]:
    outcomes_payload = _json(paths["outcomes"])
    rows = [
        row
        for row in outcomes_payload["rows"]
        if row["model_split"] == "heldout_train_validation"
    ]
    outcomes = {str(row["state_example_id"]): row for row in rows}
    manifest = _control_manifest(rows, updates)
    root = paths["root"] / f"validation/u{updates:02d}"
    manifest_path = root / "condition_manifest.json"
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(manifest_path, manifest)
    backend = __import__(
        "scripts.run_direct_injection_channel_7dh", fromlist=["_build_backend_from_generation"]
    )._build_backend_from_generation(replay["causal_audit"]["generation"])
    parent, composer, decoder, representations, gate = _load_models(
        cfg=cfg,
        settings=settings,
        paths=paths,
        updates=updates,
        device=backend.device,
    )
    bank = StaticFeatureBank(cfg=cfg, settings=settings, paths=paths, gate=gate)
    deltas, gate_rows = _compile_deltas(
        manifest=manifest,
        outcomes=outcomes,
        bank=bank,
        parent=parent,
        composer=composer,
        decoder=decoder,
        representations=representations,
        gate=gate,
        device=backend.device,
    )
    delta_path = root / "program_deltas.pt"
    checkpoint_path = paths["root"] / f"checkpoints/model_u{updates:02d}.pt"
    atomic_torch_save(
        {
            "format": "appworld_structured_validation_deltas_7hr_v1",
            "condition_keys": [row["condition_key"] for row in manifest["conditions"]],
            "deltas": deltas,
            "gate_rows": gate_rows,
            "checkpoint_sha256": sha256_file(checkpoint_path),
            "manifest_sha256": manifest["manifest_sha256"],
        },
        delta_path,
    )
    examples = _examples_by_state(load_decision_examples(paths["decisions"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    records = _records_by_task(load_memory_records(corpus / "memory_records.jsonl"))
    outputs = []
    started = time.perf_counter()
    for ordinal, condition in enumerate(manifest["conditions"], start=1):
        output_path = root / "condition_outputs" / (
            sha256_file(manifest_path)[:8]
            + "-"
            + __import__("hashlib").sha256(str(condition["condition_key"]).encode()).hexdigest()
            + ".json"
        )
        row, _ = _run_condition(
            condition=condition,
            delta=deltas[ordinal - 1],
            checkpoint_sha256=sha256_file(checkpoint_path),
            deltas_sha256=sha256_file(delta_path),
            output_path=output_path,
            stderr_path=root / f"worker_logs/{ordinal:04d}.stderr.log",
            ordinal=ordinal,
            attempt_id=attempt_id,
            replay=replay,
            manifest=manifest,
            example=examples[str(condition["state_example_id"])],
            record=records[str(condition["state_task_id"])],
            backend=backend,
            semantic_path=Path("rcmf/training/appworld_replay_clean_rebuild_7b.py"),
            bridge_script=Path("scripts/appworld_live_one_step_bridge_7b.py"),
        )
        outputs.append(row)
        attempt.progress(
            status=f"structured_validation_u{updates}",
            completed_conditions=ordinal,
            total_conditions=len(manifest["conditions"]),
            latest_validated_checkpoint=str(output_path),
        )
    summaries = _summaries(outputs)
    teacher = torch.load(paths["teacher_cache"], map_location="cpu", weights_only=False)
    raw_policy_kl = _teacher_forced_kl(
        rows=rows,
        deltas=deltas,
        manifest=manifest,
        teacher=teacher,
        backend=backend,
    )
    correct = summaries["V1_structured_correct"]
    transition = summaries["V2_transition_shuffle"]
    state = summaries["V3_state_shuffle"]
    zero = summaries["V0_zero"]
    validation_metrics = {
        "correct_successor": correct["semantic_successor_match"],
        "zero_successor": zero["semantic_successor_match"],
        "transition_shuffle_successor": transition["semantic_successor_match"],
        "state_shuffle_successor": state["semantic_successor_match"],
        "correct_signature": correct["canonical_procedural_signature_match"],
        "zero_signature": zero["canonical_procedural_signature_match"],
        "transition_shuffle_signature": transition["canonical_procedural_signature_match"],
        "state_shuffle_signature": state["canonical_procedural_signature_match"],
        "correct_execution": correct["execution_success"],
        "zero_execution": zero["execution_success"],
        "maximum_ratio": max(
            max(float(value) for value in row["layer_ratios"]) for row in outputs
        ),
        "raw_policy_kl": raw_policy_kl,
    }
    report = {
        "format": VALIDATION_FORMAT,
        "updates_per_pair": updates,
        "state_count": len(rows),
        "condition_count": len(outputs),
        "gate_on_state_count": sum(
            row["correct_pair_gate"]["gate_on"] for row in gate_rows[::4]
        ),
        "condition_metrics": summaries,
        "validation_metrics": validation_metrics,
        "checkpoint": str(checkpoint_path),
        "checkpoint_sha256": sha256_file(checkpoint_path),
        "manifest_sha256": manifest["manifest_sha256"],
        "deltas_sha256": sha256_file(delta_path),
        "elapsed_seconds": time.perf_counter() - started,
        "passed_infrastructure": len(outputs) == 4 * len(rows),
    }
    atomic_write_json(root / "validation_report.json", report)
    return report


def _select(paths: Mapping[str, Path]) -> dict[str, Any]:
    rows = [
        _json(paths["root"] / f"validation/u{updates:02d}/validation_report.json")
        for updates in (2, 4)
    ]
    selection = select_compiler_checkpoint(rows)
    report = {
        "format": "appworld_structured_compiler_selection_7hr_v1",
        **selection,
        "selection_data": "heldout_clean_train_tasks_only",
        "test_or_first37_outcomes_used": False,
    }
    atomic_write_json(paths["root"] / "checkpoint_selection.json", report)
    atomic_write_text(
        paths["root"] / "validation_report.md",
        "\n".join(
            [
                "# EXP-028A structured compiler train-validation",
                "",
                f"- eligible checkpoint found: `{str(report['passed']).lower()}`",
                f"- selected: `{None if report['selected'] is None else report['selected']['updates_per_pair']}`",
                "- selection used heldout clean-train tasks only",
                "- first37 and locked 45-state outcomes were not used",
                "",
            ]
        ),
    )
    return report


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    replay_cfg = load_config(args.replay_config)
    settings = cfg.raw["stage_c_7hr"]
    replay = replay_cfg.raw["stage_c_7b"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-028A requires global seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    paths = _paths(settings, args.artifact_dir)
    required = ("outcomes", "gate", "training_summary", "teacher_cache")
    missing = {name: str(paths[name]) for name in required if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing compiler validation inputs: {missing}")
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"structured_compiler_validation_{args.phase}",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes={name: sha256_file(paths[name]) for name in required},
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "select":
            result = _select(paths)
        else:
            result = _validate(
                cfg=cfg,
                replay=replay,
                settings=settings,
                paths=paths,
                artifact_dir=args.artifact_dir,
                updates=2 if args.phase == "u2" else 4,
                attempt=attempt,
                attempt_id=args.attempt_id,
            )
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
