from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
from transformers import AutoTokenizer

from rcmf.config import load_config
from rcmf.training.deep_residual_amortization_7f import classify_one_step_behavior
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.procedural_causal_analysis_7b import (
    comparison_set,
    condition_summary,
    per_task_summary,
)
from rcmf.training.procedural_causal_audit_7b import condition_checkpoint_name
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file
from scripts.prepare_appworld_structured_rescue_7hr import _class_selection
from scripts.run_appworld_structured_compiler_7hr import StaticFeatureBank, _paths as _compiler_paths
from scripts.run_appworld_structured_compiler_validation_7hr import _load_models
from scripts.run_deep_residual_amortized_one_step_7f import (
    LIVE_PROJECTION_MAXIMUM_RATIO,
    LIVE_PROJECTION_METHOD,
    _run_condition,
)
from scripts.run_direct_injection_channel_7dh import _build_backend_from_generation
from scripts.run_procedural_causal_audit_7b import _examples_by_state, _records_by_task
from scripts.run_state_conditioned_program_direct_7dg import _load_representations
from scripts.run_state_conditioned_program_fast_one_step_7df import _f3_rows, _load_parent_rows


GLOBAL_SEED = 25101
AUDIT_FORMAT = "appworld_structured_compiler_one_step_audit_7hr_v1"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


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
    parser.add_argument("--phase", choices=("preflight", "formal", "analyze"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp028a_audit")
    return parser.parse_args()


def _paths(cfg: Any, settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Path]:
    parent_b = Path(str(settings["parent_exp025b"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    parent_cr = Path(str(settings["parent_exp025cr"]))
    parent_26b = Path(str(cfg.raw["stage_c_7g"]["parent_exp026b"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    root = artifact_dir / "locked_one_step"
    compiler = _compiler_paths(settings, artifact_dir)
    return {
        **compiler,
        "audit_root": root,
        "selection": compiler["root"] / "checkpoint_selection.json",
        "selector": parent_c / "selector/ensemble_scores.pt",
        "selector_conditions": parent_cr / "selector_condition_manifest.json",
        "parent_c0_outputs": parent_b / "condition_outputs",
        "parent_f3_outputs": parent_cr / "selector_condition_outputs",
        "primary_manifest": parent_26b / "pair_manifest.json",
        "decisions": corpus / "decision_examples.jsonl",
        "memories": corpus / "memory_records.jsonl",
        "manifest": root / "condition_manifest.json",
        "deltas": root / "program_deltas.pt",
        "preflight": root / "preflight.json",
        "generation": root / "generation_summary.json",
        "analysis": root / "analysis.json",
    }


def _require(paths: Mapping[str, Path], names: Sequence[str]) -> None:
    missing = {name: str(paths[name]) for name in names if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing structured one-step audit inputs: {missing}")


def _stable(*parts: Any) -> str:
    return hashlib.sha256("::".join(map(str, (GLOBAL_SEED, *parts))).encode()).hexdigest()


def _heldout_selections(
    *, settings: Mapping[str, Any], paths: Mapping[str, Path], f3: Sequence[Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    transitions_list = _rows(paths["transitions"])
    transitions = {str(row["transition_id"]): row for row in transitions_list}
    classes_payload = _json(paths["classes"])
    classes = {str(row["signature_class_id"]): row for row in classes_payload["classes"]}
    class_by_transition = {
        str(transition_id): class_id
        for class_id, row in classes.items()
        for transition_id in row["member_transition_ids"]
    }
    ensemble = torch.load(paths["selector"], map_location="cpu", weights_only=False)
    ordered_state_ids = [str(value) for value in ensemble["ordered_state_ids"]]
    ordered_transition_ids = [str(value) for value in ensemble["ordered_transition_ids"]]
    state_position = {value: index for index, value in enumerate(ordered_state_ids)}
    transition_class_ids = [class_by_transition[value] for value in ordered_transition_ids]
    examples = _examples_by_state(__import__(
        "rcmf.training.datasets", fromlist=["load_decision_examples"]
    ).load_decision_examples(paths["decisions"]))
    tokenizer = AutoTokenizer.from_pretrained(
        str(settings["expected_model_name"]), trust_remote_code=True
    )
    values: dict[str, dict[str, Any]] = {}
    for source in f3:
        state_id = str(source["state_example_id"])
        selected = _class_selection(
            example=examples[state_id],
            state_scores=ensemble["scores"][state_position[state_id]].tolist(),
            ordered_transition_ids=ordered_transition_ids,
            transition_class_ids=transition_class_ids,
            transitions=transitions,
            classes=classes,
            tokenizer=tokenizer,
            prompt_profile=str(settings["appworld"]["prompt_profile"]),
            context_limit=int(settings["appworld"]["context_limit"]),
        )
        checks = {
            "scoreable": bool(selected["scoreable"]),
            "transition": str(selected["selected_transition_id"])
            == str(source["transition_id"]),
            "class": str(selected["selected_class_id"])
            == str(source["signature_class_id"]),
        }
        if not all(checks.values()):
            raise ValueError(f"Rebuilt heldout F3 selection differs for {state_id}: {checks}")
        values[state_id] = {
            "state_example_id": state_id,
            "state_task_id": str(source["state_task_id"]),
            "state_step_id": int(source["state_step_id"]),
            **selected,
        }
    if len(values) != 45:
        raise ValueError("Structured audit requires 45 frozen F3 states")
    return values


def _control_manifest(
    f3: Sequence[Mapping[str, Any]], selections: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    by_state = {str(row["state_example_id"]): dict(row) for row in f3}
    conditions = []
    control_rows = []
    for state_id in sorted(by_state):
        source = by_state[state_id]
        own_transition = str(source["transition_id"])
        own_class = str(source["signature_class_id"])
        transition_control = min(
            (
                row
                for row in f3
                if str(row["transition_id"]) != own_transition
                and str(row["signature_class_id"]) != own_class
            ),
            key=lambda row: _stable("audit-transition", state_id, row["transition_id"]),
        )
        state_control = min(
            (row for row in f3 if str(row["state_task_id"]) != str(source["state_task_id"])),
            key=lambda row: _stable("audit-state", state_id, row["state_example_id"]),
        )
        definitions = (
            ("S1_structured_correct", state_id, own_transition),
            ("S2_transition_shuffle", state_id, str(transition_control["transition_id"])),
            ("S3_state_shuffle", str(state_control["state_example_id"]), own_transition),
            ("S0_zero", state_id, own_transition),
        )
        control_rows.append(
            {
                "state_example_id": state_id,
                "correct_transition_id": own_transition,
                "transition_control_id": str(transition_control["transition_id"]),
                "state_control_id": str(state_control["state_example_id"]),
                "transition_class_differs": True,
                "state_task_differs": True,
                "behavioral_outcomes_used": False,
            }
        )
        for name, program_state, program_transition in definitions:
            condition = {
                "format": "appworld_structured_compiler_one_step_condition_7hr_v1",
                "condition_name": name,
                "prompt_kind": "compiled_program",
                "state_example_id": state_id,
                "state_task_id": str(source["state_task_id"]),
                "state_step_id": int(source["state_step_id"]),
                "audit_stratum": str(source["audit_stratum"]),
                "api_documentation_action": bool(source.get("api_documentation_action", False)),
                "procedural_tier": source.get("procedural_tier"),
                "signature_class_id": source.get("signature_class_id"),
                "selector_transition_id": own_transition,
                "program_state_example_id": program_state,
                "program_transition_id": program_transition,
                "selection_source": "frozen_exp025cr_deployment_e",
                "student_prompt_contains_raw_transition": False,
                "valid_for_generation": True,
            }
            condition["condition_key"] = canonical_sha256(condition)
            conditions.append(condition)
    payload = {
        "format": "appworld_structured_compiler_one_step_manifest_7hr_v1",
        "global_seed": GLOBAL_SEED,
        "runtime_projection_maximum_ratio": LIVE_PROJECTION_MAXIMUM_RATIO,
        "runtime_projection_method": LIVE_PROJECTION_METHOD,
        "state_count": len(by_state),
        "condition_count": len(conditions),
        "condition_name_counts": {
            name: sum(row["condition_name"] == name for row in conditions)
            for name in ("S1_structured_correct", "S2_transition_shuffle", "S3_state_shuffle", "S0_zero")
        },
        "controls": control_rows,
        "conditions": conditions,
        "selection_uses_behavioral_outcomes": False,
        "student_prompt_contains_raw_transition": False,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def _compile(
    *, cfg: Any, settings: Mapping[str, Any], paths: Mapping[str, Path], manifest: Mapping[str, Any], selections: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    selection = _json(paths["selection"])
    if not bool(selection["passed"]):
        raise RuntimeError("No eligible structured compiler checkpoint")
    updates = int(selection["selected"]["updates_per_pair"])
    device = torch.device("cpu")
    parent, composer, decoder, representations, gate = _load_models(
        cfg=cfg, settings=settings, paths=paths, updates=updates, device=device
    )
    bank = StaticFeatureBank(cfg=cfg, settings=settings, paths=paths, gate=gate)
    for state_id, row in selections.items():
        bank.register_selection(state_id, row)
    state_position = representations["state_position"]
    transition_position = representations["transition_position"]
    values = []
    gate_rows = []
    with torch.no_grad():
        for condition in manifest["conditions"]:
            state_id = str(condition["state_example_id"])
            own_transition = str(condition["selector_transition_id"])
            gate_result = bank.gate_probabilities(bank.feature(state_id, own_transition))
            if condition["condition_name"] == "S0_zero" or not gate_result["gate_on"]:
                delta = torch.zeros(4, 4, 4096)
            else:
                program_state = str(condition["program_state_example_id"])
                program_transition = str(condition["program_transition_id"])
                feature = torch.tensor([bank.feature(program_state, program_transition)], dtype=torch.float32)
                normalized = (feature - gate["standardizer_mean"]) / gate["standardizer_std"]
                state = representations["state_values"][state_position[program_state]].unsqueeze(0)
                transition = representations["transition_values"][transition_position[program_transition]].unsqueeze(0)
                latent = composer(
                    normalized,
                    parent(state, transition),
                    torch.tensor([gate_result["positive_probability"]]),
                )
                delta = decoder(latent)[0]
            values.append(delta.cpu())
            gate_rows.append(
                {
                    "condition_key": str(condition["condition_key"]),
                    "correct_pair_gate": gate_result,
                }
            )
    checkpoint = Path(str(selection["selected"]["checkpoint"]))
    payload = {
        "format": "appworld_structured_compiler_one_step_deltas_7hr_v1",
        "global_seed": GLOBAL_SEED,
        "condition_keys": [str(row["condition_key"]) for row in manifest["conditions"]],
        "deltas": torch.stack(values),
        "gate_rows": gate_rows,
        "selected_updates_per_pair": updates,
        "selected_checkpoint": str(checkpoint),
        "selected_checkpoint_sha256": sha256_file(checkpoint),
        "condition_manifest_sha256": str(manifest["manifest_sha256"]),
    }
    atomic_torch_save(payload, paths["deltas"])
    return payload


def _preflight(cfg: Any, settings: Mapping[str, Any], paths: Mapping[str, Path]) -> dict[str, Any]:
    required = (
        "selection", "gate", "selector", "selector_conditions", "state_cache", "transition_cache",
        "transitions", "signatures", "classes", "query_signatures", "intent_predictions",
        "decisions", "memories", "parent_c0_outputs", "parent_f3_outputs", "primary_manifest",
    )
    _require(paths, required)
    if sha256_file(paths["selector"]) != str(settings["expected_selector_sha256"]):
        raise ValueError("Frozen selector hash changed")
    f3 = _f3_rows(paths["selector_conditions"])
    selections = _heldout_selections(settings=settings, paths=paths, f3=f3)
    manifest = _control_manifest(f3, selections)
    paths["audit_root"].mkdir(parents=True, exist_ok=True)
    atomic_write_json(paths["manifest"], manifest)
    deltas = _compile(
        cfg=cfg, settings=settings, paths=paths, manifest=manifest, selections=selections
    )
    gate_rows = deltas["gate_rows"][::4]
    expected_seconds = len(manifest["conditions"]) * float(
        settings["runtime"]["one_step_generation_seconds_expected"]
    )
    report = {
        "format": "appworld_structured_compiler_one_step_preflight_7hr_v1",
        "state_count": 45,
        "condition_count": 180,
        "gate_on_state_count": sum(row["correct_pair_gate"]["gate_on"] for row in gate_rows),
        "qwen_generation_count": 180,
        "appworld_reconstruction_execution_count": 180,
        "expected_h100_hours": expected_seconds / 3600.0,
        "review_threshold_h100_hours": float(settings["runtime"]["review_threshold_h100_hours"]),
        "automatic_launch_allowed": expected_seconds / 3600.0
        <= float(settings["runtime"]["review_threshold_h100_hours"]),
        "manifest_sha256": str(manifest["manifest_sha256"]),
        "deltas_sha256": sha256_file(paths["deltas"]),
        "selected_checkpoint_sha256": str(deltas["selected_checkpoint_sha256"]),
        "f3_selections_exactly_rebuilt": True,
        "controls_outcome_blind": True,
        "student_prompt_contains_raw_transition": False,
        "passed": True,
    }
    atomic_write_json(paths["preflight"], report)
    return report


def _formal(
    *, replay: Mapping[str, Any], settings: Mapping[str, Any], paths: Mapping[str, Path], attempt: AttemptLedger, attempt_id: str
) -> dict[str, Any]:
    preflight = _json(paths["preflight"])
    if not bool(preflight["passed"] and preflight["automatic_launch_allowed"]):
        raise RuntimeError("Structured one-step preflight did not authorize generation")
    manifest = _json(paths["manifest"])
    deltas = torch.load(paths["deltas"], map_location="cpu", weights_only=False)
    if list(deltas["condition_keys"]) != [row["condition_key"] for row in manifest["conditions"]]:
        raise ValueError("Structured condition and delta order differs")
    backend = _build_backend_from_generation(replay["causal_audit"]["generation"])
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Structured one-step loaded trainable Qwen")
    examples = _examples_by_state(__import__(
        "rcmf.training.datasets", fromlist=["load_decision_examples"]
    ).load_decision_examples(paths["decisions"]))
    records = _records_by_task(__import__(
        "rcmf.training.datasets", fromlist=["load_memory_records"]
    ).load_memory_records(paths["memories"]))
    completed = []
    reused = 0
    started = time.perf_counter()
    delta_sha = sha256_file(paths["deltas"])
    output_dir = paths["audit_root"] / "condition_outputs"
    for ordinal, condition in enumerate(manifest["conditions"], start=1):
        key = str(condition["condition_key"])
        row, was_reused = _run_condition(
            condition=condition,
            delta=deltas["deltas"][ordinal - 1],
            checkpoint_sha256=str(deltas["selected_checkpoint_sha256"]),
            deltas_sha256=delta_sha,
            output_path=output_dir / condition_checkpoint_name(key),
            stderr_path=paths["audit_root"] / f"worker_logs/{key}.stderr.log",
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
        completed.append(row)
        reused += int(was_reused)
        attempt.progress(
            status="structured_locked_one_step",
            completed_conditions=len(completed),
            total_conditions=len(manifest["conditions"]),
            latest_validated_checkpoint=str(output_dir / condition_checkpoint_name(key)),
        )
    report = {
        "format": AUDIT_FORMAT,
        "condition_count": len(completed),
        "generated_condition_count": len(completed) - reused,
        "reused_condition_count": reused,
        "same_world_execution_count": sum(row["live_worker"]["same_world_execution"] for row in completed),
        "exception_count": sum(not bool(row["metrics"]["execution_success"]) for row in completed),
        "elapsed_seconds": time.perf_counter() - started,
        "passed": len(completed) == 180,
    }
    atomic_write_json(paths["generation"], report)
    return report


def _positive_tasks(rows: Sequence[Mapping[str, Any]]) -> tuple[int, dict[str, Any]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        grouped[str(row["state_task_id"])][f"{row['state_example_id']}::{row['condition_name']}"] = dict(row)
    details = {}
    for task_id, values in grouped.items():
        correct = {
            key.split("::", 1)[0]: row for key, row in values.items() if row["condition_name"] == "S1_structured_correct"
        }
        bare = {
            key.split("::", 1)[0]: row for key, row in values.items() if row["condition_name"] == "C0_bare"
        }
        shared = sorted(set(correct) & set(bare))
        signature = statistics.fmean(
            float(correct[key]["metrics"]["canonical_procedural_signature_match"])
            - float(bare[key]["metrics"]["canonical_procedural_signature_match"])
            for key in shared
        )
        successor = statistics.fmean(
            float(correct[key]["metrics"]["semantic_successor_match"])
            - float(bare[key]["metrics"]["semantic_successor_match"])
            for key in shared
        )
        details[task_id] = {
            "state_count": len(shared),
            "action_signature_difference": signature,
            "semantic_successor_difference": successor,
            "positive": signature > 0.0 or successor > 0.0,
        }
    return sum(row["positive"] for row in details.values()), details


def _analyze(cfg: Any, settings: Mapping[str, Any], paths: Mapping[str, Path]) -> dict[str, Any]:
    if not bool(_json(paths["generation"])["passed"]):
        raise RuntimeError("Structured one-step generation is incomplete")
    outputs = [_json(path) for path in sorted((paths["audit_root"] / "condition_outputs").glob("*.json"))]
    c0 = _load_parent_rows(paths["parent_c0_outputs"], "C0_bare")
    f3 = _load_parent_rows(paths["parent_f3_outputs"], "F3_deployment_e_field_raw")
    combined = outputs + c0 + f3
    primary_ids = {str(row["state_example_id"]) for row in _json(paths["primary_manifest"])["pairs"]}
    primary = [row for row in combined if str(row["state_example_id"]) in primary_ids]
    delta_payload = torch.load(paths["deltas"], map_location="cpu", weights_only=False)
    gate_on_ids = {
        str(condition["state_example_id"])
        for condition, gate_row in zip(_json(paths["manifest"])["conditions"][::4], delta_payload["gate_rows"][::4], strict=True)
        if bool(gate_row["correct_pair_gate"]["gate_on"])
    }
    gate_on = [row for row in combined if str(row["state_example_id"]) in gate_on_ids]
    samples = int(cfg.raw["stage_c_7g"]["bootstrap_samples"])
    comparisons = {
        "s1_minus_c0": comparison_set(primary, left="S1_structured_correct", right="C0_bare", bootstrap_samples=samples, seed=GLOBAL_SEED, per_metric_seed_offset=False),
        "s1_minus_f3": comparison_set(primary, left="S1_structured_correct", right="F3_deployment_e_field_raw", bootstrap_samples=samples, seed=GLOBAL_SEED, per_metric_seed_offset=False),
        "s1_minus_s2": comparison_set(primary, left="S1_structured_correct", right="S2_transition_shuffle", bootstrap_samples=samples, seed=GLOBAL_SEED, per_metric_seed_offset=False),
        "s1_minus_s3": comparison_set(primary, left="S1_structured_correct", right="S3_state_shuffle", bootstrap_samples=samples, seed=GLOBAL_SEED, per_metric_seed_offset=False),
        "s1_minus_s0": comparison_set(primary, left="S1_structured_correct", right="S0_zero", bootstrap_samples=samples, seed=GLOBAL_SEED, per_metric_seed_offset=False),
    }
    c0_gap = comparisons["s1_minus_c0"]
    p2_gap = comparisons["s1_minus_s2"]
    p3_gap = comparisons["s1_minus_s3"]
    positive_count, task_details = _positive_tasks(primary)
    classification = classify_one_step_behavior(
        p1_minus_c0={
            "action_signature": float(c0_gap["canonical_procedural_signature_match"]["difference"]),
            "semantic_successor": float(c0_gap["semantic_successor_match"]["difference"]),
        },
        p1_minus_p2={
            "action_signature": float(p2_gap["canonical_procedural_signature_match"]["difference"]),
            "semantic_successor": float(p2_gap["semantic_successor_match"]["difference"]),
        },
        p1_minus_p3={
            "action_signature": float(p3_gap["canonical_procedural_signature_match"]["difference"]),
            "semantic_successor": float(p3_gap["semantic_successor_match"]["difference"]),
        },
        execution_drop=-float(c0_gap["execution_success"]["difference"]),
        positive_task_count=positive_count,
    )
    branch = (
        "appworld_structured_compiler_failed"
        if classification["classification"] == "CLEAR_FAILURE"
        else "continue_to_gated_compiled_first37"
    )
    report = {
        "format": "appworld_structured_compiler_one_step_analysis_7hr_v1",
        "global_seed": GLOBAL_SEED,
        "state_count": 45,
        "primary_state_count": len(primary_ids),
        "gate_on_state_count": len(gate_on_ids),
        "condition_metrics_all": condition_summary(combined),
        "condition_metrics_primary": condition_summary(primary),
        "condition_metrics_gate_on": condition_summary(gate_on) if gate_on_ids else {},
        "per_task": per_task_summary(combined),
        "comparisons_primary": comparisons,
        "positive_task_count": positive_count,
        "positive_task_details": task_details,
        "classification": classification,
        "provisional_branch": branch,
    }
    atomic_write_json(paths["analysis"], report)
    atomic_write_text(
        paths["audit_root"] / "report.md",
        "\n".join(
            [
                "# EXP-028A locked one-step structured compiler audit",
                "",
                f"- classification: `{classification['classification']}`",
                f"- gate-ON states: `{len(gate_on_ids)}/45`",
                f"- positive tasks: `{positive_count}/9`",
                f"- provisional branch: `{branch}`",
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
    paths = _paths(cfg, settings, args.artifact_dir)
    ledger_inputs = {
        name: sha256_file(paths[name])
        for name in ("selection", "gate", "selector")
        if paths[name].exists()
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"structured_locked_one_step_{args.phase}",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=ledger_inputs,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "preflight":
            result = _preflight(cfg, settings, paths)
        elif args.phase == "formal":
            result = _formal(
                replay=replay,
                settings=settings,
                paths=paths,
                attempt=attempt,
                attempt_id=args.attempt_id,
            )
        else:
            result = _analyze(cfg, settings, paths)
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
