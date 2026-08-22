from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.config import load_config
from rcmf.training.deep_residual_amortization_7f import (
    COMPILER_VERSION,
    GLOBAL_SEED,
    K_TOKENS,
    LAYER_INDICES,
    SharedDeepResidualDecoder,
    best_visited_checkpoint,
    continue_after_u8,
    deterministic_mismatch_indices,
    differentiable_layer_ratio_projection,
    revised_u16_runtime_authorization,
)
from rcmf.training.deep_residual_carrier_7e import DeepResidualHooks
from rcmf.training.datasets import load_decision_examples
from rcmf.training.oracle_convergence_5fa import (
    ConvergenceObjective,
    atomic_torch_save,
    enrich_sequence_utility_rows,
    summarize_convergence_rows,
    update_count_summary,
)
from rcmf.training.oracle_decoder_5fc import module_state_sha256
from rcmf.training.state_conditioned_program_direct_7dg import seed_everything
from rcmf.training.state_conditioned_program_fast_7df import (
    FactorizedProgramFast,
    PairMLPProgramFast,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    write_jsonl,
)
from scripts.run_stage_c_oracle_capacity_5e import _collate, _rows_from_logits
from scripts.run_stage_c_oracle_convergence_5fa import _training_loss
from scripts.run_state_conditioned_program_direct_7dg import (
    _load_manifests,
    _load_representations,
    _pair_indices,
    _preference_partners,
    _restore_rng,
    _settings_paths,
)
from scripts.run_state_conditioned_program_fast_7df import _build_backend, _row_file
from scripts.run_transition_behavior_6a import _build_tokenized_rows
from scripts.run_deep_residual_carrier_7e import (
    _bare_target_forward,
    _capture_states,
    _forward_residual,
    _selected_indices,
)


PAIRMLP = "pair_mlp_deep_residual_observation_excluded"
FACTORIZED = "full_factorized_r32_deep_residual_observation_excluded"
CHECKPOINT_UPDATES = (4, 8, 16)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_deep_residual_amortization_7f.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--model", choices=("pairmlp", "factorized"), required=True)
    parser.add_argument("--phase", choices=("preflight", "train", "final_eval"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp027a_compiler")
    return parser.parse_args()


def _model_name(kind: str) -> str:
    return PAIRMLP if kind == "pairmlp" else FACTORIZED


def _paths(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Path]:
    parent_direct = Path(str(settings["parent_exp025d"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    return {
        "parent_direct": parent_direct,
        "parent_preflight": parent_direct / "preflight_summary.json",
        "a_split": parent_direct / "preflight/a_task_split.json",
        "teacher_rows": parent_direct / "teacher_cache/rows",
        "teacher_summary": parent_direct / "teacher_cache/summary.json",
        "decisions": corpus / "decision_examples.jsonl",
        "state_cache": parent_c / "representation_cache/multiview/state_multiview.pt",
        "transition_cache": parent_c / "representation_cache/multiview/transition_multiview.pt",
        "selector": parent_c / "selector/ensemble_scores.pt",
        "carrier_parent": Path(str(settings["parent_exp026b"])),
        "preflight": artifact_dir / "compiler/runtime_preflight.json",
        "tokenized_cache": artifact_dir / "compiler/tokenized_rows.pt",
        "base_states": artifact_dir / "compiler/base_residual_states.pt",
    }


def _direct_paths(cfg: Any, settings: Mapping[str, Any]) -> dict[str, Path]:
    return _settings_paths(
        cfg.raw["stage_c_7dg"], Path(str(settings["parent_exp025d"]))
    )


def _load_pair_data(
    *,
    cfg: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    backend: Any | None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, Any]]:
    direct_paths = _direct_paths(cfg, settings)
    manifests = _load_manifests(direct_paths)
    split = _json(paths["a_split"])
    expected = settings["expected_pairs"]
    checks = {
        cell: len(manifests[cell]) == int(expected[cell]) for cell in "ABCDE"
    }
    checks.update(
        {
            "A_train": int(split["train_pair_count"]) == int(expected["A_train"]),
            "A_validation": int(split["validation_pair_count"])
            == int(expected["A_validation"]),
            "task_overlap_empty": not split["task_overlap"],
            "state_overlap_empty": not split["state_overlap"],
        }
    )
    if not all(checks.values()):
        raise ValueError(f"EXP-025D pair contract differs: {checks}")
    if backend is None:
        return manifests, split, {}
    cache_path = paths["tokenized_cache"]
    unique_pairs = {
        str(row["pair_id"]): dict(row)
        for rows in manifests.values()
        for row in rows
    }
    if cache_path.exists():
        payload = torch.load(cache_path, map_location="cpu", weights_only=False)
        if list(payload["ordered_pair_ids"]) != sorted(unique_pairs):
            raise ValueError("Tokenized compiler cache pair order differs")
        tokenized = list(payload["rows"])
    else:
        responses = []
        for pair_id in sorted(unique_pairs):
            row_path = _row_file(paths["teacher_rows"], pair_id)
            if not row_path.exists():
                raise FileNotFoundError(f"Missing immutable teacher row: {row_path}")
            row = _json(row_path)
            if str(row["pair_id"]) != pair_id:
                raise ValueError("Teacher row pair identity differs")
            responses.append(row)
        examples = load_decision_examples(paths["decisions"])
        tokenized = _build_tokenized_rows(
            backend=backend,
            examples=examples,
            response_rows=responses,
            prompt_profile=cfg.benchmark.prompt_profile,
            context_limit=int(cfg.raw["stage_c_7dg"]["teacher_cache"]["context_limit"]),
        )
        atomic_torch_save(
            {
                "format": "deep_residual_compiler_tokenized_rows_7f_v1",
                "ordered_pair_ids": sorted(unique_pairs),
                "rows": tokenized,
                "source_teacher_summary_sha256": sha256_file(paths["teacher_summary"]),
            },
            cache_path,
        )
    tokenized_by_pair = {str(row["pair_id"]): row for row in tokenized}
    if set(tokenized_by_pair) != set(unique_pairs):
        raise ValueError("Tokenized compiler row set differs")
    return manifests, split, tokenized_by_pair


def _preflight(
    *, cfg: Any, settings: Mapping[str, Any], paths: Mapping[str, Path], artifact_dir: Path
) -> dict[str, Any]:
    manifests, split, _ = _load_pair_data(
        cfg=cfg, settings=settings, paths=paths, backend=None
    )
    compiler = settings["compiler"]
    runtime = settings["runtime"]
    train_pairs = int(split["train_pair_count"])
    validation_pairs = int(split["validation_pair_count"])
    final_pairs = validation_pairs + sum(len(manifests[cell]) for cell in "BCDE")
    expected_phase_a = float(runtime["measured_first37_bare_h100_hours"]) * float(
        runtime["phase_a_multiplier_expected"]
    )
    conservative_phase_a = float(runtime["measured_first37_bare_h100_hours"]) * float(
        runtime["phase_a_multiplier_conservative"]
    )
    backward_expected = float(runtime["deep_backward_seconds_expected"])
    backward_conservative = float(runtime["deep_backward_seconds_conservative"])
    forward_expected = float(runtime["pair_evaluation_seconds_expected"])
    forward_conservative = float(runtime["pair_evaluation_seconds_conservative"])
    checkpoint_forwards = validation_pairs * 3
    final_control_count = 5
    pair_u8_expected = train_pairs * 8 * backward_expected / 3600.0
    pair_u16_expected = train_pairs * 16 * backward_expected / 3600.0
    evaluation_expected = (
        checkpoint_forwards + final_pairs * final_control_count
    ) * forward_expected / 3600.0
    one_step_expected = 180 * float(
        runtime["one_step_generation_seconds_expected"]
    ) / 3600.0
    required_u8 = expected_phase_a + pair_u8_expected + evaluation_expected + one_step_expected
    required_u16 = expected_phase_a + pair_u16_expected + evaluation_expected + one_step_expected
    conditional_factor_u8 = (
        train_pairs * 8 * backward_expected
        + (checkpoint_forwards + final_pairs * 6) * forward_expected
        + 180 * float(runtime["one_step_generation_seconds_expected"])
    ) / 3600.0
    report = {
        "format": "deep_residual_amortization_runtime_preflight_7f_v1",
        "run_uuid": str(settings["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "pair_counts": {cell: len(manifests[cell]) for cell in "ABCDE"},
        "A_train_pairs": train_pairs,
        "A_validation_pairs": validation_pairs,
        "A_train_tasks": int(split["train_task_count"]),
        "A_validation_tasks": int(split["validation_task_count"]),
        "pairmlp_backward_calls_u8": train_pairs * 8,
        "pairmlp_backward_calls_u16": train_pairs * 16,
        "pairmlp_checkpoint_forward_rows": checkpoint_forwards,
        "pairmlp_final_forward_rows_per_control": final_pairs,
        "pairmlp_final_control_count": final_control_count,
        "phase_c_generation_count": 180,
        "phase_a_expected_h100_hours": expected_phase_a,
        "phase_a_conservative_h100_hours": conservative_phase_a,
        "phase_b_c_expected_h100_hours_u8": pair_u8_expected
        + evaluation_expected
        + one_step_expected,
        "phase_b_c_expected_h100_hours_u16": pair_u16_expected
        + evaluation_expected
        + one_step_expected,
        "pairmlp_training_expected_h100_hours_u8": pair_u8_expected,
        "pairmlp_training_expected_h100_hours_u16": pair_u16_expected,
        "pairmlp_final_evaluation_expected_h100_hours": evaluation_expected,
        "phase_c_one_step_expected_h100_hours": one_step_expected,
        "required_expected_h100_hours_u8": required_u8,
        "required_expected_h100_hours_u16": required_u16,
        "conditional_phase_d_expected_h100_hours_u8": conditional_factor_u8,
        "review_threshold_h100_hours": float(runtime["review_threshold_h100_hours"]),
        "automatic_initial_launch_allowed": required_u8
        <= float(runtime["review_threshold_h100_hours"]),
        "u16_requires_revised_runtime_gate": required_u16
        > float(runtime["review_threshold_h100_hours"]),
        "conditional_phase_d_requires_revised_runtime_gate": conditional_factor_u8
        + required_u8
        > float(runtime["review_threshold_h100_hours"]),
        "projected_checkpoint_bytes": int(runtime["projected_bytes_per_checkpoint"]),
        "student_prompt_contains_raw_transition": False,
        "selected_layers": list(LAYER_INDICES),
        "selected_token_count": K_TOKENS,
        "program_dim": int(compiler["program_dim"]),
    }
    atomic_write_json(paths["preflight"], report)
    atomic_write_text(
        artifact_dir / "compiler/runtime_preflight.md",
        "\n".join(
            [
                "# EXP-027A Runtime Preflight",
                "",
                f"- A train/validation: `{train_pairs}/{validation_pairs}` pairs",
                f"- A/B/C/D/E: `{[len(manifests[cell]) for cell in 'ABCDE']}`",
                f"- Phase A expected/conservative H100 h: `{expected_phase_a:.3f}/{conservative_phase_a:.3f}`",
                f"- PairMLP+one-step expected through u8: `{report['phase_b_c_expected_h100_hours_u8']:.3f}` H100 h",
                f"- total expected through u8: `{required_u8:.3f}` H100 h",
                f"- total expected through u16: `{required_u16:.3f}` H100 h",
                f"- conditional factorized u8 additional: `{conditional_factor_u8:.3f}` H100 h",
                f"- initial automatic launch: `{str(report['automatic_initial_launch_allowed']).lower()}`",
                "",
            ]
        ),
    )
    return report


def _build_model(
    *, kind: str, settings: Mapping[str, Any], view_names: Sequence[str], device: torch.device
) -> nn.Module:
    seed_everything(GLOBAL_SEED)
    values = settings["compiler"]
    common = {
        "state_vector_count": int(values["state_vector_count"]),
        "transition_view_names": view_names,
        "representation_dim": int(values["representation_dim"]),
        "program_dim": int(values["program_dim"]),
        "hidden_dim": int(values["hidden_dim"]),
        "dropout": float(values["dropout"]),
    }
    if kind == "pairmlp":
        return PairMLPProgramFast(**common).to(device)
    return FactorizedProgramFast(
        **common,
        controller_rank=int(values["controller_rank"]),
        include_outcome=False,
    ).to(device)


def _build_decoder(settings: Mapping[str, Any], model_dim: int, device: torch.device) -> SharedDeepResidualDecoder:
    seed_everything(GLOBAL_SEED)
    decoder = SharedDeepResidualDecoder(
        program_dim=int(settings["compiler"]["program_dim"]),
        layer_count=len(LAYER_INDICES),
        token_count=K_TOKENS,
        model_dim=model_dim,
    ).to(device)
    nn.init.zeros_(decoder.linear.weight)
    return decoder


def _base_states(
    *, backend: Any, rows: Sequence[dict[str, Any]], path: Path
) -> dict[str, Tensor]:
    ordered = {}
    for row in rows:
        ordered.setdefault(str(row["state_example_id"]), row)
    state_ids = sorted(ordered)
    if path.exists():
        payload = torch.load(path, map_location="cpu", weights_only=False)
        if list(payload["ordered_state_ids"]) != state_ids:
            raise ValueError("Base residual-state cache state order differs")
        return {
            state_id: payload["values"][index].to(torch.float32)
            for index, state_id in enumerate(state_ids)
        }
    values = []
    for state_id in state_ids:
        batch = _collate([ordered[state_id]], device=backend.device, k=K_TOKENS)
        values.append(
            _capture_states(
                backend=backend, batch=batch, layer_indices=LAYER_INDICES
            )[0].cpu()
        )
    tensor = torch.stack(values)
    atomic_torch_save(
        {
            "format": "deep_residual_base_states_7f_v1",
            "ordered_state_ids": state_ids,
            "selected_layer_indices": list(LAYER_INDICES),
            "token_count": K_TOKENS,
            "values": tensor,
        },
        path,
    )
    return {state_id: tensor[index] for index, state_id in enumerate(state_ids)}


def _latents(
    *,
    kind: str,
    model: nn.Module,
    states: Tensor,
    transitions: Tensor,
    pair_ids: Sequence[str],
    state_ids: Sequence[str],
    transition_ids: Sequence[str],
    control: str,
    device: torch.device,
) -> Tensor:
    state_perm = deterministic_mismatch_indices(
        state_ids, pair_ids, namespace=f"7f-{kind}-{control}-state"
    )
    transition_perm = deterministic_mismatch_indices(
        transition_ids, pair_ids, namespace=f"7f-{kind}-{control}-transition"
    )
    swap = deterministic_mismatch_indices(
        transition_ids, pair_ids, namespace=f"7f-{kind}-{control}-memory-swap"
    )
    output = []
    model.eval()
    with torch.no_grad():
        for start in range(0, len(pair_ids), 32):
            stop = min(len(pair_ids), start + 32)
            state = states[start:stop].to(device)
            transition = transitions[start:stop].to(device)
            if control == "state_shuffle":
                state = states[state_perm[start:stop]].to(device)
            elif control == "transition_shuffle":
                transition = transitions[transition_perm[start:stop]].to(device)
            elif control == "memory_swap":
                transition = transitions[swap[start:stop]].to(device)
            if kind == "factorized" and control == "static_only":
                z = model.components(state, transition)["static"]
            else:
                z = model(state, transition)
            output.append(z.cpu())
    values = torch.cat(output).to(device)
    if control == "zero":
        values.zero_()
    return values


def _evaluate(
    *,
    kind: str,
    model: nn.Module,
    decoder: SharedDeepResidualDecoder,
    rows: Sequence[dict[str, Any]],
    representations: Mapping[str, Any],
    base_states: Mapping[str, Tensor],
    backend: Any,
    settings: Mapping[str, Any],
    control: str,
) -> dict[str, Any]:
    pair_ids = [str(row["pair_id"]) for row in rows]
    state_ids = [str(row["state_example_id"]) for row in rows]
    transition_ids = [
        str(row.get("transition_id", row.get("memory_id"))) for row in rows
    ]
    state_indices, transition_indices = _pair_indices(rows, representations)
    states = representations["state_values"][state_indices]
    transitions = representations["transition_values"][transition_indices]
    z_values = _latents(
        kind=kind,
        model=model,
        states=states,
        transitions=transitions,
        pair_ids=pair_ids,
        state_ids=state_ids,
        transition_ids=transition_ids,
        control=control,
        device=backend.device,
    )
    output_rows = []
    maximum_ratio = 0.0
    model.eval()
    decoder.eval()
    with torch.no_grad():
        for index, row in enumerate(rows):
            batch = _collate([row], device=backend.device, k=K_TOKENS)
            base = base_states[str(row["state_example_id"])].unsqueeze(0).to(backend.device)
            raw = decoder(z_values[index : index + 1])
            delta, ratios = differentiable_layer_ratio_projection(
                raw, base, maximum_ratio=float(settings["compiler"]["ratio_budget_per_layer"])
            )
            result = _forward_residual(
                backend=backend,
                batch=batch,
                delta=delta,
                layer_indices=LAYER_INDICES,
                original_states=base,
            )
            maximum_ratio = max(maximum_ratio, float(ratios["maximum_ratio"].cpu()))
            rows_from_logits = _rows_from_logits(
                logits=result["target_logits"],
                labels=batch["labels"],
                response_rows=batch["response_rows"],
                target_lengths=batch["target_lengths"],
                pair_rows=[row],
                delta_ratios=result["global_ratios"],
                control=f"{kind}_{control}",
                huber_delta=float(settings["compiler"]["sequence_huber_delta"]),
            )
            output_rows.extend(rows_from_logits)
    enriched = enrich_sequence_utility_rows(
        output_rows, huber_delta=float(settings["compiler"]["sequence_huber_delta"])
    )
    return {
        "summary": summarize_convergence_rows(enriched),
        "rows": enriched,
        "maximum_layer_ratio": maximum_ratio,
    }


def _checkpoint_payload(
    *,
    kind: str,
    model: nn.Module,
    decoder: nn.Module,
    optimizer: torch.optim.Optimizer,
    pair_ids: Sequence[str],
    update_counts: Sequence[int],
    completed_rounds: int,
    history: Sequence[Mapping[str, Any]],
    split_sha256: str,
    decoder_frozen: bool,
) -> dict[str, Any]:
    return {
        "format": "deep_residual_amortized_checkpoint_7f_v1",
        "compiler_version": COMPILER_VERSION,
        "model_kind": kind,
        "global_seed": GLOBAL_SEED,
        "pair_ids": list(pair_ids),
        "update_counts": list(update_counts),
        "update_accounting": update_count_summary(pair_ids, update_counts),
        "completed_rounds": int(completed_rounds),
        "model_state_dict": {key: value.detach().cpu() for key, value in model.state_dict().items()},
        "decoder_state_dict": {key: value.detach().cpu() for key, value in decoder.state_dict().items()},
        "optimizer_state_dict": optimizer.state_dict(),
        "history": list(history),
        "split_sha256": split_sha256,
        "decoder_frozen": bool(decoder_frozen),
        "python_random_state": random.getstate(),
        "torch_rng_state": torch.get_rng_state(),
        "cuda_rng_state": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
    }


def _objective(settings: Mapping[str, Any], kind: str) -> ConvergenceObjective:
    values = settings["compiler"]
    return ConvergenceObjective(
        name=f"7f_{kind}_direct_behavior",
        target_delta_weight=float(values["target_delta_huber_weight"]),
        sequence_utility_weight=float(values["sequence_utility_weight"]),
        sparse_teacher_kl_weight=float(values["sparse_teacher_kl_weight"]),
        huber_delta=float(values["sequence_huber_delta"]),
    )


def _train(
    *,
    kind: str,
    backend: Any,
    settings: Mapping[str, Any],
    representations: Mapping[str, Any],
    train_rows: Sequence[dict[str, Any]],
    validation_rows: Sequence[dict[str, Any]],
    base_states: Mapping[str, Tensor],
    split_sha256: str,
    output_dir: Path,
    attempt: AttemptLedger,
    frozen_decoder_checkpoint: Path | None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    model = _build_model(
        kind=kind,
        settings=settings,
        view_names=representations["transition_view_names"],
        device=backend.device,
    )
    decoder = _build_decoder(
        settings, int(backend.model.config.hidden_size), backend.device
    )
    decoder_frozen = kind == "factorized"
    if decoder_frozen:
        if frozen_decoder_checkpoint is None:
            raise ValueError("Factorized compiler requires the frozen PairMLP decoder")
        payload = torch.load(frozen_decoder_checkpoint, map_location="cpu", weights_only=False)
        decoder.load_state_dict(payload["decoder_state_dict"])
        decoder.eval()
        for parameter in decoder.parameters():
            parameter.requires_grad_(False)
    parameter_groups = [
        {
            "params": list(model.parameters()),
            "lr": float(settings["compiler"]["program_learning_rate"]),
        }
    ]
    if not decoder_frozen:
        parameter_groups.append(
            {
                "params": list(decoder.parameters()),
                "lr": float(settings["compiler"]["decoder_learning_rate"]),
            }
        )
    optimizer = torch.optim.AdamW(
        parameter_groups, weight_decay=float(settings["compiler"]["weight_decay"])
    )
    pair_ids = [str(row["pair_id"]) for row in train_rows]
    state_indices, transition_indices = _pair_indices(train_rows, representations)
    preference = _preference_partners(
        train_rows, {"program": settings["compiler"]}
    )
    update_counts = [0] * len(train_rows)
    history: list[dict[str, Any]] = []
    completed = 0
    latest = output_dir / "latest_checkpoint.json"
    if latest.exists():
        payload = torch.load(
            Path(str(_json(latest)["checkpoint"])),
            map_location=backend.device,
            weights_only=False,
        )
        checks = {
            "kind": str(payload["model_kind"]) == kind,
            "seed": int(payload["global_seed"]) == GLOBAL_SEED,
            "pairs": list(payload["pair_ids"]) == pair_ids,
            "split": str(payload["split_sha256"]) == split_sha256,
            "decoder_frozen": bool(payload["decoder_frozen"]) == decoder_frozen,
        }
        if not all(checks.values()):
            raise ValueError(f"Deep compiler resume identity differs: {checks}")
        model.load_state_dict(payload["model_state_dict"])
        decoder.load_state_dict(payload["decoder_state_dict"])
        optimizer.load_state_dict(payload["optimizer_state_dict"])
        update_counts = [int(value) for value in payload["update_counts"]]
        history = list(payload["history"])
        completed = int(payload["completed_rounds"])
        _restore_rng(payload)
    objective = _objective(settings, kind)
    utility_cache: dict[int, float] = {}
    started = time.perf_counter()
    stop_at = 16
    if history and int(history[-1]["updates_per_pair"]) == 8:
        decision = continue_after_u8({**history[-1], "previous": history[-2]})
        if not decision["continue_to_u16"]:
            stop_at = 8
    for update_round in range(completed + 1, stop_at + 1):
        order = sorted(
            range(len(train_rows)),
            key=lambda index: hashlib.sha256(
                f"{GLOBAL_SEED}:{kind}:round:{update_round}:{pair_ids[index]}".encode()
            ).hexdigest(),
        )
        losses = []
        maximum_ratio = 0.0
        preference_terms = 0
        model.train()
        if not decoder_frozen:
            decoder.train()
        for index in order:
            row = train_rows[index]
            batch = _collate([row], device=backend.device, k=K_TOKENS)
            state = representations["state_values"][state_indices[index]].unsqueeze(0).to(
                backend.device
            )
            transition = representations["transition_values"][
                transition_indices[index]
            ].unsqueeze(0).to(backend.device)
            z = model(state, transition)
            raw_delta = decoder(z)
            base = base_states[str(row["state_example_id"])].unsqueeze(0).to(backend.device)
            delta, ratio = differentiable_layer_ratio_projection(
                raw_delta,
                base,
                maximum_ratio=float(settings["compiler"]["ratio_budget_per_layer"]),
            )
            optimizer.zero_grad(set_to_none=True)
            with DeepResidualHooks(
                model=backend.model,
                layer_indices=LAYER_INDICES,
                selected_token_indices=_selected_indices(batch),
                delta=delta,
                expected_prefill_length=int(batch["input_ids"].shape[1]),
            ):
                _, target_logits = _bare_target_forward(backend=backend, batch=batch)
                loss, terms = _training_loss(
                    logits=target_logits, batch=batch, objective=objective
                )
                utility = float(row["response_cache"]["text_utility"])
                category = str(row["response_cache"]["utility_category"])
                preservation = 0.0
                if category == "neutral":
                    preservation = float(
                        settings["compiler"]["neutral_preservation_weight"]
                    )
                elif utility < 0.0:
                    preservation = float(
                        settings["compiler"]["harmful_preservation_weight"]
                    )
                loss = loss + preservation * terms["student_utility"].pow(2).mean()
                if index in preference and preference[index][0] in utility_cache:
                    partner, direction, margin = preference[index]
                    partner_value = torch.tensor(
                        utility_cache[partner],
                        device=backend.device,
                        dtype=terms["student_utility"].dtype,
                    )
                    preference_loss = F.relu(
                        float(margin)
                        - float(direction)
                        * (terms["student_utility"].mean() - partner_value)
                    )
                    loss = loss + float(
                        settings["compiler"]["preference_weight"]
                    ) * preference_loss
                    preference_terms += 1
                raw_ratio = ratio["raw_layer_ratio"]
                loss = loss + float(settings["compiler"]["ratio_restraint_weight"]) * (
                    F.relu(raw_ratio - 1.0).pow(2).mean() + 0.01 * z.pow(2).mean()
                )
                # Hooks must survive activation-checkpoint recomputation in backward.
                loss.backward()
            trainable = list(model.parameters()) + (
                [] if decoder_frozen else list(decoder.parameters())
            )
            torch.nn.utils.clip_grad_norm_(
                trainable, float(settings["compiler"]["max_grad_norm"])
            )
            optimizer.step()
            update_counts[index] += 1
            utility_cache[index] = float(terms["student_utility"].detach().cpu())
            losses.append(float(loss.detach().cpu()))
            maximum_ratio = max(maximum_ratio, float(ratio["maximum_ratio"].detach().cpu()))
            if not math.isfinite(losses[-1]) or maximum_ratio > 1.0001:
                raise RuntimeError("Deep compiler produced nonfinite loss or ratio violation")
        accounting = update_count_summary(pair_ids, update_counts)
        if not accounting["all_pairs_equal"] or accounting["minimum_updates_per_pair"] != update_round:
            raise RuntimeError("Deep compiler update accounting differs")
        if update_round not in CHECKPOINT_UPDATES:
            continue
        validation = _evaluate(
            kind=kind,
            model=model,
            decoder=decoder,
            rows=validation_rows,
            representations=representations,
            base_states=base_states,
            backend=backend,
            settings=settings,
            control="correct",
        )
        entry = {
            "updates_per_pair": update_round,
            "mean_training_loss": statistics.fmean(losses),
            "preference_term_count": preference_terms,
            "maximum_ratio": maximum_ratio,
            "a_validation_huber": float(
                validation["summary"]["sequence_utility_huber"]["mean"]
            ),
            "a_validation_spearman": float(
                validation["summary"]["u_text_vs_u_student_spearman"]
            ),
            "a_validation": validation["summary"],
            "update_accounting": accounting,
            "elapsed_seconds": time.perf_counter() - started,
        }
        history.append(entry)
        checkpoint = output_dir / "checkpoints" / f"model_u{update_round:02d}.pt"
        atomic_torch_save(
            _checkpoint_payload(
                kind=kind,
                model=model,
                decoder=decoder,
                optimizer=optimizer,
                pair_ids=pair_ids,
                update_counts=update_counts,
                completed_rounds=update_round,
                history=history,
                split_sha256=split_sha256,
                decoder_frozen=decoder_frozen,
            ),
            checkpoint,
        )
        atomic_write_json(latest, {"checkpoint": str(checkpoint), "updates_per_pair": update_round})
        attempt.progress(
            status=f"phase_b_{kind}_u{update_round}",
            latest_validated_checkpoint=str(checkpoint),
            updates_per_pair=update_round,
        )
        write_jsonl(output_dir / f"a_validation_u{update_round:02d}_rows.jsonl", validation["rows"])
        print(
            f"compiler {kind} u{update_round} rho={entry['a_validation_spearman']:.6f} "
            f"huber={entry['a_validation_huber']:.6f}",
            flush=True,
        )
        if update_round == 8:
            decision = continue_after_u8({**history[-1], "previous": history[-2]})
            atomic_write_json(output_dir / "u8_continuation_decision.json", decision)
            if kind == "pairmlp" and decision["continue_to_u16"]:
                preflight = _json(output_dir.parent / "runtime_preflight.json")
                phase_a = _json(
                    output_dir.parent.parent
                    / "phase_a_first37_v2"
                    / "summary.json"
                )
                authorization = revised_u16_runtime_authorization(
                    phase_a_actual_h100_hours=float(phase_a["total_wall_seconds"])
                    / 3600.0,
                    pairmlp_elapsed_through_u8_hours=float(entry["elapsed_seconds"])
                    / 3600.0,
                    fixed_final_evaluation_hours=float(
                        preflight["pairmlp_final_evaluation_expected_h100_hours"]
                    ),
                    phase_c_one_step_hours=float(
                        preflight["phase_c_one_step_expected_h100_hours"]
                    ),
                    review_threshold_h100_hours=float(
                        preflight["review_threshold_h100_hours"]
                    ),
                )
                atomic_write_json(output_dir / "u16_runtime_authorization.json", authorization)
                if not authorization["automatic_u16_authorized"]:
                    attempt.progress(
                        status="pairmlp_u16_runtime_review_required",
                        latest_validated_checkpoint=str(checkpoint),
                        projected_total_h100_hours=float(
                            authorization["projected_total_h100_hours_through_u16"]
                        ),
                    )
                    raise RuntimeError(
                        "EXP-027A u16 continuation exceeds the 18-H100-hour review threshold"
                    )
            if not decision["continue_to_u16"]:
                stop_at = 8
                break
    selected = best_visited_checkpoint(history)
    selected_path = output_dir / "checkpoints" / f"model_u{int(selected['updates_per_pair']):02d}.pt"
    summary = {
        "format": "deep_residual_amortized_training_summary_7f_v1",
        "model_kind": kind,
        "global_seed": GLOBAL_SEED,
        "train_pair_count": len(train_rows),
        "validation_pair_count": len(validation_rows),
        "history": history,
        "selected_updates_per_pair": int(selected["updates_per_pair"]),
        "selected_checkpoint": str(selected_path),
        "selected_checkpoint_sha256": sha256_file(selected_path),
        "selection_rule": "lowest_A_validation_behavioral_Huber_subject_to_positive_Spearman_finite_ratio",
        "decoder_frozen": decoder_frozen,
        "qwen_trainable": any(parameter.requires_grad for parameter in backend.model.parameters()),
        "student_prompt_contains_raw_transition": False,
        "elapsed_seconds": time.perf_counter() - started,
    }
    atomic_write_json(output_dir / "training_summary.json", summary)
    return summary


def _load_selected(
    *,
    kind: str,
    settings: Mapping[str, Any],
    representations: Mapping[str, Any],
    backend: Any,
    output_dir: Path,
) -> tuple[nn.Module, SharedDeepResidualDecoder, dict[str, Any]]:
    summary = _json(output_dir / "training_summary.json")
    model = _build_model(
        kind=kind,
        settings=settings,
        view_names=representations["transition_view_names"],
        device=backend.device,
    )
    decoder = _build_decoder(settings, int(backend.model.config.hidden_size), backend.device)
    payload = torch.load(
        Path(str(summary["selected_checkpoint"])),
        map_location=backend.device,
        weights_only=False,
    )
    model.load_state_dict(payload["model_state_dict"])
    decoder.load_state_dict(payload["decoder_state_dict"])
    model.eval()
    decoder.eval()
    return model, decoder, summary


def _final_eval(
    *,
    kind: str,
    model: nn.Module,
    decoder: SharedDeepResidualDecoder,
    manifests: Mapping[str, Sequence[Mapping[str, Any]]],
    split: Mapping[str, Any],
    tokenized: Mapping[str, dict[str, Any]],
    representations: Mapping[str, Any],
    base_states: Mapping[str, Tensor],
    backend: Any,
    settings: Mapping[str, Any],
    output_dir: Path,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    controls = (
        ("correct", "state_shuffle", "transition_shuffle", "memory_swap", "zero")
        if kind == "pairmlp"
        else (
            "correct",
            "static_only",
            "state_shuffle",
            "transition_shuffle",
            "memory_swap",
            "zero",
        )
    )
    cell_rows = {
        "A_validation": [
            tokenized[str(manifests["A"][index]["pair_id"])]
            for index in split["validation_indices"]
        ],
        **{
            cell: [tokenized[str(row["pair_id"])] for row in manifests[cell]]
            for cell in "BCDE"
        },
    }
    evaluations = {}
    for cell, rows in cell_rows.items():
        evaluations[cell] = {}
        for control in controls:
            result = _evaluate(
                kind=kind,
                model=model,
                decoder=decoder,
                rows=rows,
                representations=representations,
                base_states=base_states,
                backend=backend,
                settings=settings,
                control=control,
            )
            path = output_dir / "evaluation" / cell / f"{control}.jsonl"
            write_jsonl(path, result["rows"])
            evaluations[cell][control] = {
                **result["summary"],
                "maximum_layer_ratio": result["maximum_layer_ratio"],
                "rows_path": str(path),
                "rows_sha256": sha256_file(path),
            }
        attempt.progress(
            status=f"phase_b_{kind}_final_evaluation",
            completed_cell=cell,
            latest_validated_checkpoint=str(output_dir / "evaluation" / cell),
        )
    summary = {
        "format": "deep_residual_amortized_final_evaluation_7f_v1",
        "model_kind": kind,
        "global_seed": GLOBAL_SEED,
        "evaluations": evaluations,
        "B_C_D_E_used_for_checkpoint_selection": False,
        "selector_sha256": sha256_file(Path(str(settings["parent_exp025c"])) / "selector/ensemble_scores.pt"),
        "student_prompt_contains_raw_transition": False,
    }
    atomic_write_json(output_dir / "final_evaluation_summary.json", summary)
    return summary


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7f"]
    seed_everything(GLOBAL_SEED)
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    paths = _paths(settings, args.artifact_dir)
    if args.phase == "preflight":
        _preflight(cfg=cfg, settings=settings, paths=paths, artifact_dir=args.artifact_dir)
        return
    preflight = _json(paths["preflight"])
    if not bool(preflight["automatic_initial_launch_allowed"]):
        raise RuntimeError("Initial expected EXP-027A runtime exceeds 18 H100 hours")
    backend = _build_backend(cfg)
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Deep compiler loaded trainable Qwen parameters")
    manifests, split, tokenized = _load_pair_data(
        cfg=cfg, settings=settings, paths=paths, backend=backend
    )
    representations = _load_representations(
        {"state_cache": paths["state_cache"], "transition_cache": paths["transition_cache"]},
        backend.device,
    )
    all_rows = list(tokenized.values())
    bases = _base_states(backend=backend, rows=all_rows, path=paths["base_states"])
    train_rows = [
        tokenized[str(manifests["A"][index]["pair_id"])] for index in split["train_indices"]
    ]
    validation_rows = [
        tokenized[str(manifests["A"][index]["pair_id"])]
        for index in split["validation_indices"]
    ]
    kind = args.model
    output_dir = args.artifact_dir / "compiler" / kind
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"compiler_{kind}_{args.phase}",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes={
            "selector": sha256_file(paths["selector"]),
            "teacher_summary": sha256_file(paths["teacher_summary"]),
            "a_split": sha256_file(paths["a_split"]),
        },
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "train":
            frozen_decoder = None
            if kind == "factorized":
                pair_summary = _json(
                    args.artifact_dir / "compiler/pairmlp/training_summary.json"
                )
                frozen_decoder = Path(str(pair_summary["selected_checkpoint"]))
            _train(
                kind=kind,
                backend=backend,
                settings=settings,
                representations=representations,
                train_rows=train_rows,
                validation_rows=validation_rows,
                base_states=bases,
                split_sha256=str(split["manifest_sha256"]),
                output_dir=output_dir,
                attempt=attempt,
                frozen_decoder_checkpoint=frozen_decoder,
            )
        else:
            model, decoder, _ = _load_selected(
                kind=kind,
                settings=settings,
                representations=representations,
                backend=backend,
                output_dir=output_dir,
            )
            _final_eval(
                kind=kind,
                model=model,
                decoder=decoder,
                manifests=manifests,
                split=split,
                tokenized=tokenized,
                representations=representations,
                base_states=bases,
                backend=backend,
                settings=settings,
                output_dir=output_dir,
                attempt=attempt,
            )
    del backend
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
