from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
import math
import os
from pathlib import Path
import random
import shutil
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
    K_TOKENS,
    LAYER_INDICES,
    differentiable_layer_ratio_projection,
)
from rcmf.training.deep_residual_carrier_7e import DeepResidualHooks
from rcmf.training.datasets import load_decision_examples
from rcmf.training.memory_specific_deep_amortization_7g import (
    GLOBAL_SEED,
    build_mismatch_manifest,
    select_checkpoint,
    selection_diagnostics,
)
from rcmf.training.oracle_convergence_5fa import atomic_torch_save, update_count_summary
from rcmf.training.oracle_decoder_5fc import module_state_sha256
from rcmf.training.pair_grounding_5d import spearman
from rcmf.training.state_conditioned_program_7d import canonical_sha256, stable_key
from rcmf.training.state_conditioned_program_direct_7dg import seed_everything
from rcmf.training.state_conditioned_program_policy_distill_7dg3 import sparse_policy_kl
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_jsonl,
)
from scripts.prepare_state_conditioned_program_7d import _context_builder
from scripts.run_deep_residual_carrier_7e import (
    _bare_target_forward,
    _forward_residual,
    _selected_indices,
)
from scripts.run_deep_residual_compiler_7f import (
    PAIRMLP,
    _base_states,
    _build_decoder,
    _build_model,
    _load_pair_data,
)
from scripts.run_direct_injection_channel_7dh import _build_backend_from_generation
from scripts.run_stage_c_oracle_capacity_5e import _collate
from scripts.run_state_conditioned_program_direct_7dg import (
    _load_representations,
    _pair_indices,
    _restore_rng,
)
from scripts.run_state_conditioned_program_policy_distill_7dg3 import (
    POLICY_CACHE_VERSION,
    _policy_loss,
    _policy_tokenized_row,
    _teacher_policy_row,
    _validate_teacher_row,
)
from scripts.run_transition_behavior_6a import _build_tokenized_rows


CHECKPOINT_VERSION = "memory_specific_deep_pairmlp_checkpoint_7g_v1"
CHECKPOINT_UPDATES = (2, 4, 8)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _row_path(root: Path, pair_id: str) -> Path:
    return root / f"{sha256_text(pair_id)}.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_memory_specific_deep_amortization_7g.yaml"
        ),
    )
    parser.add_argument(
        "--replay-config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=("preflight", "teacher", "train", "evaluate"), required=True
    )
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp027b_policy")
    return parser.parse_args()


def _paths(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Path]:
    parent_direct = Path(str(settings["parent_exp025d"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    parent_b = Path(str(settings["parent_exp025b"]))
    parent_cr = Path(str(settings["parent_exp025cr"]))
    parent_a = Path(str(settings["parent_exp027a"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    root = artifact_dir / "compiler/pairmlp"
    return {
        "root": root,
        "parent_direct": parent_direct,
        "parent_preflight": parent_direct / "preflight_summary.json",
        "a_split": parent_direct / "preflight/a_task_split.json",
        "teacher_rows": parent_direct / "teacher_cache/rows",
        "teacher_summary": parent_direct / "teacher_cache/summary.json",
        "decisions": corpus / "decision_examples.jsonl",
        "state_cache": parent_c / "representation_cache/multiview/state_multiview.pt",
        "transition_cache": parent_c
        / "representation_cache/multiview/transition_multiview.pt",
        "selector": parent_c / "selector/ensemble_scores.pt",
        "replay_lineage": parent_b / "replay_validated_corpus_manifest.json",
        "selector_conditions": parent_cr / "selector_condition_manifest.json",
        "transitions": parent_b
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        "parent_policy_rows": Path(str(settings["parent_policy_cache"])),
        "parent_base_states": parent_a / "compiler/base_residual_states.pt",
        "preflight": artifact_dir / "runtime_preflight.json",
        "pair_contract": root / "pair_contract.json",
        "training_mismatches": root / "training_mismatch_manifest.json",
        "evaluation_mismatch_root": root / "evaluation_mismatches",
        "tokenized_cache": root / "direct_tokenized_rows.pt",
        "policy_tokenized_cache": root / "policy_tokenized_rows.pt",
        "raw_teacher_rows": root / "raw_policy_teacher_cache/rows",
        "raw_teacher_summary": root / "raw_policy_teacher_cache/summary.json",
        "latest_checkpoint": root / "latest_checkpoint.json",
        "training_summary": root / "training_summary.json",
        "evaluation_summary": root / "final_evaluation_summary.json",
    }


def _require(paths: Mapping[str, Path], names: Sequence[str]) -> None:
    missing = {name: str(paths[name]) for name in names if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing EXP-027B inputs: {missing}")


def _unique_pairs(manifests: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for cell in "ABCDE":
        for row in manifests[cell]:
            pair_id = str(row["pair_id"])
            if pair_id in values:
                invariant = (
                    "state_example_id",
                    "transition_id",
                    "prompt_sha256",
                    "teacher_prompt_sha256",
                )
                if any(str(values[pair_id][key]) != str(row[key]) for key in invariant):
                    raise ValueError(f"Duplicate pair identity differs: {pair_id}")
                continue
            values[pair_id] = dict(row)
    return [values[pair_id] for pair_id in sorted(values)]


def _scoreable_policy_pair(pair: Mapping[str, Any], teacher: Mapping[str, Any]) -> bool:
    return int(pair["teacher_prompt_tokens"]) + int(teacher["max_new_tokens"]) <= int(
        teacher["context_limit"]
    )


def _manifest_for_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return build_mismatch_manifest(rows, seed=GLOBAL_SEED)


def _runtime_projection(
    *,
    settings: Mapping[str, Any],
    new_teacher_rows: int,
    train_pairs: int,
    validation_pairs: int,
    final_rows: int,
) -> dict[str, Any]:
    values = settings["runtime"]
    backwards = train_pairs * int(settings["compiler"]["maximum_updates_per_pair"]) * 2
    checkpoint_forwards = validation_pairs * len(CHECKPOINT_UPDATES) * 4
    final_forwards = final_rows * 4
    generations = 180
    scenarios = {}
    for name in ("expected", "conservative"):
        seconds = (
            float(values[f"matched_bare_{name}_hours"]) * 3600.0
            + new_teacher_rows * float(values[f"teacher_generation_seconds_{name}"])
            + backwards * float(values[f"deep_backward_seconds_{name}"])
            + (checkpoint_forwards + final_forwards)
            * float(values[f"policy_forward_seconds_{name}"])
            + generations * float(values[f"one_step_generation_seconds_{name}"])
        )
        scenarios[name] = {
            "h100_hours": seconds / 3600.0,
            "matched_bare_hours": float(values[f"matched_bare_{name}_hours"]),
            "teacher_cache_hours": new_teacher_rows
            * float(values[f"teacher_generation_seconds_{name}"])
            / 3600.0,
            "training_hours": backwards
            * float(values[f"deep_backward_seconds_{name}"])
            / 3600.0,
            "evaluation_hours": (checkpoint_forwards + final_forwards)
            * float(values[f"policy_forward_seconds_{name}"])
            / 3600.0,
            "one_step_hours": generations
            * float(values[f"one_step_generation_seconds_{name}"])
            / 3600.0,
        }
    return {
        "new_raw_policy_teacher_rows": new_teacher_rows,
        "qwen_backward_calls": backwards,
        "a_validation_checkpoint_forward_calls": checkpoint_forwards,
        "final_evaluation_forward_calls": final_forwards,
        "one_step_generation_execution_count": generations,
        "scenarios": scenarios,
        "projected_artifact_bytes": (
            new_teacher_rows * int(values["projected_bytes_per_teacher_row"])
            + len(CHECKPOINT_UPDATES) * int(values["projected_bytes_per_checkpoint"])
            + generations * int(values["projected_bytes_per_condition"])
        ),
    }


def _preflight(
    *, cfg: Any, settings: Mapping[str, Any], artifact_dir: Path
) -> dict[str, Any]:
    paths = _paths(settings, artifact_dir)
    _require(
        paths,
        (
            "parent_preflight",
            "a_split",
            "teacher_summary",
            "decisions",
            "state_cache",
            "transition_cache",
            "selector",
            "replay_lineage",
            "transitions",
            "parent_policy_rows",
            "parent_base_states",
        ),
    )
    if sha256_file(paths["selector"]) != str(settings["expected_selector_sha256"]):
        raise ValueError("Frozen selector hash changed")
    if str(_json(paths["replay_lineage"])["lineage_sha256"]) != str(
        settings["expected_replay_lineage_sha256"]
    ):
        raise ValueError("Replay-validated clean lineage changed")
    manifests, split, _ = _load_pair_data(
        cfg=cfg, settings=settings, paths=paths, backend=None
    )
    counts = {cell: len(manifests[cell]) for cell in "ABCDE"}
    expected = settings["expected_pairs"]
    checks = {cell: counts[cell] == int(expected[cell]) for cell in "ABCDE"}
    checks["unique"] = len(_unique_pairs(manifests)) == int(expected["unique"])
    checks["train"] = int(split["train_pair_count"]) == int(expected["A_train"])
    checks["validation"] = int(split["validation_pair_count"]) == int(
        expected["A_validation"]
    )
    if not all(checks.values()):
        raise ValueError(f"Pair universe changed: {checks}")

    unique = _unique_pairs(manifests)
    scoreable_ids = {
        str(row["pair_id"])
        for row in unique
        if _scoreable_policy_pair(row, settings["teacher"])
    }
    missing = [row for row in unique if str(row["pair_id"]) not in scoreable_ids]
    train_rows = [
        manifests["A"][index]
        for index in split["train_indices"]
        if str(manifests["A"][index]["pair_id"]) in scoreable_ids
    ]
    validation_rows = [
        manifests["A"][index]
        for index in split["validation_indices"]
        if str(manifests["A"][index]["pair_id"]) in scoreable_ids
    ]
    if len(validation_rows) != int(expected["A_validation"]):
        raise ValueError("Policy headroom unexpectedly removed A-validation rows")
    cell_rows = {
        cell: [row for row in manifests[cell] if str(row["pair_id"]) in scoreable_ids]
        for cell in "BCDE"
    }
    paths["root"].mkdir(parents=True, exist_ok=True)
    pair_contract = {
        "format": "memory_specific_pair_contract_7g_v1",
        "global_seed": GLOBAL_SEED,
        "original_pair_counts": counts,
        "unique_pair_count": len(unique),
        "policy_scoreable_unique_pair_count": len(scoreable_ids),
        "over_context_missing_pair_count": len(missing),
        "over_context_missing_pairs": missing,
        "A_train_original": int(split["train_pair_count"]),
        "A_train_policy_scoreable": len(train_rows),
        "A_validation_policy_scoreable": len(validation_rows),
        "final_cell_policy_scoreable": {
            cell: len(rows) for cell, rows in cell_rows.items()
        },
        "scoreable_pair_ids": sorted(scoreable_ids),
        "train_pair_ids": [str(row["pair_id"]) for row in train_rows],
        "validation_pair_ids": [str(row["pair_id"]) for row in validation_rows],
        "cell_pair_ids": {
            cell: [str(row["pair_id"]) for row in rows]
            for cell, rows in cell_rows.items()
        },
        "over_context_treatment": "missing_no_truncation_no_zero_no_neutral",
    }
    atomic_write_json(paths["pair_contract"], pair_contract)
    train_mismatch = _manifest_for_rows(train_rows)
    atomic_write_json(paths["training_mismatches"], train_mismatch)
    evaluation_sets = {"A_validation": validation_rows, **cell_rows}
    for cell, rows in evaluation_sets.items():
        atomic_write_json(paths["evaluation_mismatch_root"] / f"{cell}.json", _manifest_for_rows(rows))

    reusable = 0
    incompatible = []
    for pair in unique:
        if str(pair["pair_id"]) not in scoreable_ids:
            continue
        source = _row_path(paths["parent_policy_rows"], str(pair["pair_id"]))
        if not source.exists():
            continue
        try:
            _validate_teacher_row(_json(source), pair, settings)
            reusable += 1
        except ValueError:
            incompatible.append(str(pair["pair_id"]))
    new_teacher_rows = len(scoreable_ids) - reusable
    final_rows = len(validation_rows) + sum(len(rows) for rows in cell_rows.values())
    runtime = _runtime_projection(
        settings=settings,
        new_teacher_rows=new_teacher_rows,
        train_pairs=len(train_rows),
        validation_pairs=len(validation_rows),
        final_rows=final_rows,
    )
    launch = (
        float(runtime["scenarios"]["expected"]["h100_hours"])
        <= float(settings["runtime"]["review_threshold_h100_hours"])
    )
    report = {
        "format": "memory_specific_deep_amortization_preflight_7g_v1",
        "run_uuid": str(settings["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "pair_contract_sha256": sha256_file(paths["pair_contract"]),
        "training_mismatch_sha256": sha256_file(paths["training_mismatches"]),
        "pair_counts": counts,
        "unique_pairs": len(unique),
        "A_train_original": int(split["train_pair_count"]),
        "A_train_policy_scoreable": len(train_rows),
        "A_validation": len(validation_rows),
        "over_context_missing": len(missing),
        "reusable_raw_policy_teacher_rows": reusable,
        "incompatible_parent_teacher_rows": incompatible,
        "new_raw_policy_teacher_rows": new_teacher_rows,
        "runtime": runtime,
        "review_threshold_h100_hours": float(
            settings["runtime"]["review_threshold_h100_hours"]
        ),
        "automatic_launch_allowed": launch,
        "selection_formula_preregistered": str(settings["selection"]["score_formula"]),
        "mismatch_schedule": "odd_round_transition_even_round_state",
        "student_prompt_contains_raw_transition": False,
        "passed": launch and not incompatible,
    }
    atomic_write_json(paths["preflight"], report)
    atomic_write_text(
        artifact_dir / "runtime_preflight.md",
        "\n".join(
            [
                "# EXP-027B runtime preflight",
                "",
                f"- A original/scoreable train/validation: `{split['train_pair_count']}/{len(train_rows)}/{len(validation_rows)}`",
                f"- A/B/C/D/E: `{counts}`",
                f"- unique raw-policy pairs: `{len(scoreable_ids)}`",
                f"- reused/new raw-policy teachers: `{reusable}/{new_teacher_rows}`",
                f"- explicit over-context missing rows: `{len(missing)}`",
                f"- Qwen backward calls: `{runtime['qwen_backward_calls']}`",
                f"- expected/conservative H100 hours: `{runtime['scenarios']['expected']['h100_hours']:.4f}/{runtime['scenarios']['conservative']['h100_hours']:.4f}`",
                f"- 12-hour automatic launch: `{str(launch).lower()}`",
                "- no prompt truncation, imputation, selector change, or raw transition in student prompt",
                "",
            ]
        ),
    )
    return report


def _teacher_cache(
    *,
    cfg: Any,
    settings: Mapping[str, Any],
    replay: Mapping[str, Any],
    artifact_dir: Path,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    paths = _paths(settings, artifact_dir)
    preflight = _json(paths["preflight"])
    if not bool(preflight["passed"]):
        raise RuntimeError("Runtime preflight did not authorize teacher generation")
    contract = _json(paths["pair_contract"])
    scoreable = set(contract["scoreable_pair_ids"])
    manifests, _, _ = _load_pair_data(cfg=cfg, settings=settings, paths=paths, backend=None)
    pairs = [row for row in _unique_pairs(manifests) if str(row["pair_id"]) in scoreable]
    backend = _build_backend_from_generation(replay["causal_audit"]["generation"])
    examples = load_decision_examples(paths["decisions"])
    contexts, _ = _context_builder(
        tokenizer=backend.tokenizer,
        examples=examples,
        prompt_profile=cfg.benchmark.prompt_profile,
    )
    transitions = {str(row["transition_id"]): row for row in _rows(paths["transitions"])}
    paths["raw_teacher_rows"].mkdir(parents=True, exist_ok=True)
    reused_parent = 0
    resumed = 0
    generated = 0
    generated_tokens = 0
    generation_seconds = 0.0
    started = time.perf_counter()
    for ordinal, pair in enumerate(pairs, start=1):
        output = _row_path(paths["raw_teacher_rows"], str(pair["pair_id"]))
        if output.exists():
            row = _json(output)
            _validate_teacher_row(row, pair, settings)
            resumed += 1
        else:
            source = _row_path(paths["parent_policy_rows"], str(pair["pair_id"]))
            if source.exists():
                source_row = _json(source)
                _validate_teacher_row(source_row, pair, settings)
                temporary = output.with_suffix(".json.tmp")
                temporary.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, temporary)
                os.replace(temporary, output)
                row = source_row
                reused_parent += 1
            else:
                row = _teacher_policy_row(
                    backend=backend,
                    context=contexts[str(pair["state_example_id"])],
                    pair=pair,
                    transition=transitions[str(pair["transition_id"])],
                    prompt_profile=cfg.benchmark.prompt_profile,
                    teacher_settings=settings["teacher"],
                    structural_lineage=str(settings["expected_structural_lineage_sha256"]),
                )
                atomic_write_json(output, row)
                generated += 1
                generated_tokens += int(row["generated_token_count"])
                generation_seconds += float(row["generation_seconds"])
        attempt.progress(
            status="raw_policy_teacher_cache",
            completed_pairs=ordinal,
            total_pairs=len(pairs),
            latest_validated_checkpoint=str(output),
        )
        if ordinal % 25 == 0 or ordinal == len(pairs):
            print(f"raw policy teachers {ordinal}/{len(pairs)}", flush=True)
    rows = [_json(_row_path(paths["raw_teacher_rows"], str(pair["pair_id"]))) for pair in pairs]
    summary = {
        "format": "memory_specific_raw_policy_teacher_summary_7g_v1",
        "pair_count": len(rows),
        "unique_pair_count": len({str(row["pair_id"]) for row in rows}),
        "resumed_rows": resumed,
        "reused_parent_rows": reused_parent,
        "new_rows": generated,
        "generated_tokens": generated_tokens,
        "generation_seconds": generation_seconds,
        "elapsed_seconds": time.perf_counter() - started,
        "row_set_sha256": canonical_sha256(
            {
                str(row["pair_id"]): sha256_file(
                    _row_path(paths["raw_teacher_rows"], str(row["pair_id"]))
                )
                for row in rows
            }
        ),
        "qwen_frozen": not any(
            parameter.requires_grad for parameter in backend.model.parameters()
        ),
        "passed": len(rows) == int(contract["policy_scoreable_unique_pair_count"]),
    }
    atomic_write_json(paths["raw_teacher_summary"], summary)
    return summary


def _bare_teacher_from_direct(row: Mapping[str, Any]) -> dict[str, Any]:
    response = row["response_cache"]
    positions = []
    for index, source in enumerate(response["target_positions"]):
        logits = [float(value) for value in source["baseline_top64_logits"]]
        normalizer = float(source["baseline_logsumexp"])
        logprobs = [value - normalizer for value in logits]
        selected_mass = sum(math.exp(value) for value in logprobs)
        positions.append(
            {
                "position": index,
                "teacher_token_id": int(source["target_token_id"]),
                "top_token_ids": [
                    int(value) for value in source["baseline_top64_token_ids"]
                ],
                "top_logprobs": logprobs,
                "other_probability": max(0.0, 1.0 - selected_mass),
            }
        )
    target_ids = [int(value) for value in response["target_token_ids"]]
    if len(target_ids) != len(positions):
        raise ValueError("Bare-policy target positions differ from target token IDs")
    return {
        "format": "cached_bare_policy_on_ground_truth_sequence_7g_v1",
        "pair_id": str(row["pair_id"]),
        "generated_token_ids": target_ids,
        "generated_token_count": len(target_ids),
        "positions": positions,
        "source": "immutable_direct_teacher_baseline_top64",
    }


def _load_data(
    *,
    backend: Any,
    cfg: Any,
    settings: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    paths = _paths(settings, artifact_dir)
    contract = _json(paths["pair_contract"])
    manifests, split, direct = _load_pair_data(
        cfg=cfg, settings=settings, paths=paths, backend=backend
    )
    pairs = {str(row["pair_id"]): row for row in _unique_pairs(manifests)}
    scoreable_ids = list(contract["scoreable_pair_ids"])
    teachers = {
        pair_id: _json(_row_path(paths["raw_teacher_rows"], pair_id))
        for pair_id in scoreable_ids
    }
    for pair_id in scoreable_ids:
        _validate_teacher_row(teachers[pair_id], pairs[pair_id], settings)

    cache = paths["policy_tokenized_cache"]
    if cache.exists():
        payload = torch.load(cache, map_location="cpu", weights_only=False)
        if list(payload["ordered_pair_ids"]) != scoreable_ids:
            raise ValueError("Policy tokenized-cache pair order differs")
        policy_rows = list(payload["rows"])
    else:
        examples = load_decision_examples(paths["decisions"])
        contexts, _ = _context_builder(
            tokenizer=backend.tokenizer,
            examples=examples,
            prompt_profile=cfg.benchmark.prompt_profile,
        )
        policy_rows = [
            _policy_tokenized_row(
                backend=backend,
                context=contexts[str(pairs[pair_id]["state_example_id"])],
                pair=pairs[pair_id],
                teacher=teachers[pair_id],
            )
            for pair_id in scoreable_ids
        ]
        atomic_torch_save(
            {
                "format": "memory_specific_policy_tokenized_rows_7g_v1",
                "ordered_pair_ids": scoreable_ids,
                "rows": policy_rows,
                "teacher_summary_sha256": sha256_file(paths["raw_teacher_summary"]),
                "student_prompt_contains_raw_transition": False,
            },
            cache,
        )
    policy = {str(row["pair_id"]): row for row in policy_rows}
    if set(policy) != set(scoreable_ids):
        raise ValueError("Policy tokenized row set differs")
    bare = {pair_id: _bare_teacher_from_direct(direct[pair_id]) for pair_id in scoreable_ids}
    return {
        "manifests": manifests,
        "split": split,
        "pairs": pairs,
        "direct": direct,
        "policy": policy,
        "teachers": teachers,
        "bare_teachers": bare,
        "scoreable_ids": set(scoreable_ids),
    }


def _target_ids(row: Mapping[str, Any], device: torch.device) -> Tensor:
    values = [int(value) for value in row["labels"] if int(value) != -100]
    if len(values) != int(row["target_len"]):
        raise ValueError("Target label count differs")
    return torch.tensor(values, dtype=torch.long, device=device)


def _program_delta(
    *,
    model: nn.Module,
    decoder: nn.Module,
    state: Tensor,
    transition: Tensor,
    base: Tensor,
    maximum_ratio: float,
) -> tuple[Tensor, Tensor, Mapping[str, Tensor]]:
    latent = model(state, transition)
    raw = decoder(latent)
    delta, ratios = differentiable_layer_ratio_projection(
        raw, base, maximum_ratio=maximum_ratio
    )
    return latent, delta, ratios


def _forward_with_hooks(
    *, backend: Any, batch: Mapping[str, Any], delta: Tensor
) -> tuple[Tensor, Tensor]:
    with DeepResidualHooks(
        model=backend.model,
        layer_indices=LAYER_INDICES,
        selected_token_indices=_selected_indices(batch),
        delta=delta,
        expected_prefill_length=int(batch["input_ids"].shape[1]),
    ):
        return _bare_target_forward(backend=backend, batch=batch)


def _evaluation_rows(
    *,
    model: nn.Module,
    decoder: nn.Module,
    pair_rows: Sequence[Mapping[str, Any]],
    mismatch_manifest: Mapping[str, Any],
    data: Mapping[str, Any],
    representations: Mapping[str, Any],
    base_states: Mapping[str, Tensor],
    backend: Any,
    settings: Mapping[str, Any],
) -> list[dict[str, Any]]:
    mismatch = {str(row["pair_id"]): row for row in mismatch_manifest["rows"]}
    state_position = representations["state_position"]
    transition_position = representations["transition_position"]
    output = []
    model.eval()
    decoder.eval()
    for pair in pair_rows:
        pair_id = str(pair["pair_id"])
        policy_row = data["policy"][pair_id]
        direct_row = data["direct"][pair_id]
        bare_teacher = data["bare_teachers"][pair_id]
        state = representations["state_values"][
            state_position[str(pair["state_example_id"])]
        ].unsqueeze(0).to(backend.device)
        transition = representations["transition_values"][
            transition_position[str(pair["transition_id"])]
        ].unsqueeze(0).to(backend.device)
        base = base_states[str(pair["state_example_id"])].unsqueeze(0).to(backend.device)
        with torch.no_grad():
            _, correct_delta, correct_ratio = _program_delta(
                model=model,
                decoder=decoder,
                state=state,
                transition=transition,
                base=base,
                maximum_ratio=float(settings["compiler"]["ratio_budget_per_layer"]),
            )
            combined = _collate(
                [policy_row, direct_row], device=backend.device, k=K_TOKENS
            )
            correct = _forward_residual(
                backend=backend,
                batch=combined,
                delta=correct_delta.repeat(2, 1, 1, 1),
                layer_indices=LAYER_INDICES,
                original_states=base.repeat(2, 1, 1, 1),
            )
            policy_length = int(policy_row["target_len"])
            correct_policy_logits = correct["target_logits"][:policy_length]
            correct_gt_logits = correct["target_logits"][policy_length:]
            correct_kl, correct_terms = _policy_loss(
                correct_policy_logits, data["teachers"][pair_id]
            )
            correct_gt_nll = F.cross_entropy(
                correct_gt_logits.to(torch.float32),
                _target_ids(direct_row, backend.device),
            )

            zero_batch = _collate([policy_row], device=backend.device, k=K_TOKENS)
            zero = _forward_residual(
                backend=backend,
                batch=zero_batch,
                delta=torch.zeros_like(correct_delta),
                layer_indices=LAYER_INDICES,
                original_states=base,
            )
            zero_kl, zero_terms = _policy_loss(
                zero["target_logits"], data["teachers"][pair_id]
            )

            control_values = {}
            for name in ("transition", "state"):
                control = mismatch[pair_id]
                control_state = state
                control_transition = transition
                if name == "transition":
                    control_transition = representations["transition_values"][
                        transition_position[
                            str(control["transition_mismatch_transition_id"])
                        ]
                    ].unsqueeze(0).to(backend.device)
                else:
                    control_state = representations["state_values"][
                        state_position[str(control["state_mismatch_state_example_id"])]
                    ].unsqueeze(0).to(backend.device)
                _, control_delta, control_ratio = _program_delta(
                    model=model,
                    decoder=decoder,
                    state=control_state,
                    transition=control_transition,
                    base=base,
                    maximum_ratio=float(
                        settings["compiler"]["ratio_budget_per_layer"]
                    ),
                )
                batch = _collate([direct_row], device=backend.device, k=K_TOKENS)
                result = _forward_residual(
                    backend=backend,
                    batch=batch,
                    delta=control_delta,
                    layer_indices=LAYER_INDICES,
                    original_states=base,
                )
                control_kl, control_terms = _policy_loss(
                    result["target_logits"], bare_teacher
                )
                control_values[name] = {
                    "policy_kl": float(control_kl.cpu()),
                    "teacher_token_ce": float(control_terms["teacher_token_ce"].cpu()),
                    "student_utility": float(
                        direct_row["response_cache"]["baseline_mean_target_nll"]
                        - result["loss"].cpu()
                    ),
                    "maximum_ratio": float(control_ratio["maximum_ratio"].cpu()),
                }
        teacher_utility = float(direct_row["response_cache"]["text_utility"])
        student_utility = float(
            direct_row["response_cache"]["baseline_mean_target_nll"]
            - correct_gt_nll.cpu()
        )
        output.append(
            {
                "format": "memory_specific_teacher_forced_row_7g_v1",
                "pair_id": pair_id,
                "cell": str(pair["cell"]),
                "state_example_id": str(pair["state_example_id"]),
                "state_task_id": str(pair["state_task_id"]),
                "transition_id": str(pair["transition_id"]),
                "correct_raw_policy_kl": float(correct_kl.cpu()),
                "correct_teacher_token_ce": float(
                    correct_terms["teacher_token_ce"].cpu()
                ),
                "correct_teacher_token_top1": float(correct_terms["top1"].cpu()),
                "zero_raw_policy_kl": float(zero_kl.cpu()),
                "zero_teacher_token_ce": float(zero_terms["teacher_token_ce"].cpu()),
                "transition_mismatch_bare_policy_kl": control_values["transition"][
                    "policy_kl"
                ],
                "transition_mismatch_bare_teacher_token_ce": control_values[
                    "transition"
                ]["teacher_token_ce"],
                "state_mismatch_bare_policy_kl": control_values["state"]["policy_kl"],
                "state_mismatch_bare_teacher_token_ce": control_values["state"][
                    "teacher_token_ce"
                ],
                "teacher_sequence_utility": teacher_utility,
                "correct_student_sequence_utility": student_utility,
                "transition_mismatch_student_sequence_utility": control_values[
                    "transition"
                ]["student_utility"],
                "state_mismatch_student_sequence_utility": control_values["state"][
                    "student_utility"
                ],
                "correct_ground_truth_nll": float(correct_gt_nll.cpu()),
                "maximum_ratio": max(
                    float(correct_ratio["maximum_ratio"].cpu()),
                    control_values["transition"]["maximum_ratio"],
                    control_values["state"]["maximum_ratio"],
                ),
            }
        )
    return output


def _summarize_evaluation(rows: Sequence[Mapping[str, Any]], beta: float) -> dict[str, Any]:
    if not rows:
        raise ValueError("Cannot summarize empty evaluation rows")
    mean_names = (
        "correct_raw_policy_kl",
        "correct_teacher_token_ce",
        "correct_teacher_token_top1",
        "zero_raw_policy_kl",
        "zero_teacher_token_ce",
        "transition_mismatch_bare_policy_kl",
        "transition_mismatch_bare_teacher_token_ce",
        "state_mismatch_bare_policy_kl",
        "state_mismatch_bare_teacher_token_ce",
        "teacher_sequence_utility",
        "correct_student_sequence_utility",
        "transition_mismatch_student_sequence_utility",
        "state_mismatch_student_sequence_utility",
        "correct_ground_truth_nll",
    )
    summary = {
        "row_count": len(rows),
        **{
            name: statistics.fmean(float(row[name]) for row in rows)
            for name in mean_names
        },
        "maximum_ratio": max(float(row["maximum_ratio"]) for row in rows),
    }
    teacher = torch.tensor(
        [float(row["teacher_sequence_utility"]) for row in rows], dtype=torch.float32
    )
    student = torch.tensor(
        [float(row["correct_student_sequence_utility"]) for row in rows],
        dtype=torch.float32,
    )
    summary["correct_sequence_utility_huber"] = float(
        F.smooth_l1_loss(student, teacher, beta=beta).item()
    )
    value = spearman(teacher.tolist(), student.tolist())
    summary["correct_sequence_utility_spearman"] = None if value is None else float(value)
    summary.update(selection_diagnostics(summary))
    return summary


def _checkpoint_payload(
    *,
    model: nn.Module,
    decoder: nn.Module,
    optimizer: torch.optim.Optimizer,
    pair_ids: Sequence[str],
    update_counts: Sequence[int],
    completed_rounds: int,
    history: Sequence[Mapping[str, Any]],
    pair_contract_sha256: str,
    mismatch_manifest_sha256: str,
) -> dict[str, Any]:
    return {
        "format": CHECKPOINT_VERSION,
        "global_seed": GLOBAL_SEED,
        "model_name": PAIRMLP,
        "initialization": "exact_exp027a_pairmlp_and_zero_decoder_initialization",
        "pair_ids": list(pair_ids),
        "update_counts": [int(value) for value in update_counts],
        "update_accounting": update_count_summary(pair_ids, update_counts),
        "completed_rounds": int(completed_rounds),
        "history": list(history),
        "pair_contract_sha256": pair_contract_sha256,
        "mismatch_manifest_sha256": mismatch_manifest_sha256,
        "model_state_dict": {
            key: value.detach().cpu() for key, value in model.state_dict().items()
        },
        "decoder_state_dict": {
            key: value.detach().cpu() for key, value in decoder.state_dict().items()
        },
        "optimizer_state_dict": optimizer.state_dict(),
        "model_sha256": module_state_sha256(model),
        "decoder_sha256": module_state_sha256(decoder),
        "python_random_state": random.getstate(),
        "torch_rng_state": torch.get_rng_state(),
        "cuda_rng_state": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
    }


def _pair_rows_from_ids(
    pair_ids: Sequence[str], pairs: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    return [dict(pairs[str(pair_id)]) for pair_id in pair_ids]


def _train(
    *,
    backend: Any,
    cfg: Any,
    settings: Mapping[str, Any],
    artifact_dir: Path,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    paths = _paths(settings, artifact_dir)
    _require(
        paths,
        (
            "raw_teacher_summary",
            "pair_contract",
            "training_mismatches",
            "parent_base_states",
        ),
    )
    if not bool(_json(paths["raw_teacher_summary"])["passed"]):
        raise RuntimeError("Raw-policy teacher cache is incomplete")
    data = _load_data(backend=backend, cfg=cfg, settings=settings, artifact_dir=artifact_dir)
    contract = _json(paths["pair_contract"])
    train_pairs = _pair_rows_from_ids(contract["train_pair_ids"], data["pairs"])
    validation_pairs = _pair_rows_from_ids(
        contract["validation_pair_ids"], data["pairs"]
    )
    train_mismatch = _json(paths["training_mismatches"])
    mismatch = {str(row["pair_id"]): row for row in train_mismatch["rows"]}
    representations = _load_representations(paths, backend.device)
    base_states = _base_states(
        backend=backend,
        rows=list(data["direct"].values()),
        path=paths["parent_base_states"],
    )
    model = _build_model(
        kind="pairmlp",
        settings=settings,
        view_names=representations["transition_view_names"],
        device=backend.device,
    )
    decoder = _build_decoder(
        settings, int(backend.model.config.hidden_size), backend.device
    )
    model.train()
    decoder.train()
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Qwen is not frozen")
    optimizer = torch.optim.AdamW(
        [
            {
                "params": list(model.parameters()),
                "lr": float(settings["compiler"]["program_learning_rate"]),
            },
            {
                "params": list(decoder.parameters()),
                "lr": float(settings["compiler"]["decoder_learning_rate"]),
            },
        ],
        weight_decay=float(settings["compiler"]["weight_decay"]),
    )
    pair_ids = [str(row["pair_id"]) for row in train_pairs]
    update_counts = [0] * len(pair_ids)
    completed = 0
    history: list[dict[str, Any]] = []
    if paths["latest_checkpoint"].exists():
        latest = _json(paths["latest_checkpoint"])
        payload = torch.load(
            Path(str(latest["checkpoint"])),
            map_location=backend.device,
            weights_only=False,
        )
        checks = {
            "format": str(payload.get("format")) == CHECKPOINT_VERSION,
            "seed": int(payload.get("global_seed", -1)) == GLOBAL_SEED,
            "pairs": list(payload.get("pair_ids", [])) == pair_ids,
            "contract": str(payload.get("pair_contract_sha256"))
            == sha256_file(paths["pair_contract"]),
            "mismatches": str(payload.get("mismatch_manifest_sha256"))
            == sha256_file(paths["training_mismatches"]),
        }
        if not all(checks.values()):
            raise ValueError(f"Training resume identity differs: {checks}")
        model.load_state_dict(payload["model_state_dict"])
        decoder.load_state_dict(payload["decoder_state_dict"])
        optimizer.load_state_dict(payload["optimizer_state_dict"])
        update_counts = [int(value) for value in payload["update_counts"]]
        completed = int(payload["completed_rounds"])
        history = list(payload["history"])
        _restore_rng(payload)

    state_position = representations["state_position"]
    transition_position = representations["transition_position"]
    compiler = settings["compiler"]
    started = time.perf_counter()
    for update_round in range(completed + 1, int(compiler["maximum_updates_per_pair"]) + 1):
        order = sorted(
            range(len(train_pairs)),
            key=lambda index: stable_key(
                GLOBAL_SEED,
                f"7g-policy-round-{update_round}",
                pair_ids[index],
            ),
        )
        metrics: dict[str, list[float]] = {
            name: []
            for name in (
                "correct_policy_kl",
                "correct_teacher_token_ce",
                "correct_ground_truth_ce",
                "mismatch_policy_kl",
                "mismatch_teacher_token_ce",
                "mismatch_utility_abs",
                "loss",
                "maximum_ratio",
            )
        }
        mismatch_type = "transition" if update_round % 2 == 1 else "state"
        model.train()
        decoder.train()
        for index in order:
            pair = train_pairs[index]
            pair_id = pair_ids[index]
            state = representations["state_values"][
                state_position[str(pair["state_example_id"])]
            ].unsqueeze(0).to(backend.device)
            transition = representations["transition_values"][
                transition_position[str(pair["transition_id"])]
            ].unsqueeze(0).to(backend.device)
            base = base_states[str(pair["state_example_id"])].unsqueeze(0).to(
                backend.device
            )
            optimizer.zero_grad(set_to_none=True)

            latent, correct_delta, correct_ratio = _program_delta(
                model=model,
                decoder=decoder,
                state=state,
                transition=transition,
                base=base,
                maximum_ratio=float(compiler["ratio_budget_per_layer"]),
            )
            policy_row = data["policy"][pair_id]
            direct_row = data["direct"][pair_id]
            combined = _collate(
                [policy_row, direct_row], device=backend.device, k=K_TOKENS
            )
            with DeepResidualHooks(
                model=backend.model,
                layer_indices=LAYER_INDICES,
                selected_token_indices=_selected_indices(combined),
                delta=correct_delta.repeat(2, 1, 1, 1),
                expected_prefill_length=int(combined["input_ids"].shape[1]),
            ):
                _, combined_logits = _bare_target_forward(
                    backend=backend, batch=combined
                )
                policy_length = int(policy_row["target_len"])
                correct_policy_logits = combined_logits[:policy_length]
                correct_gt_logits = combined_logits[policy_length:]
                policy_kl, policy_terms = _policy_loss(
                    correct_policy_logits, data["teachers"][pair_id]
                )
                gt_ce = F.cross_entropy(
                    correct_gt_logits.to(torch.float32),
                    _target_ids(direct_row, backend.device),
                )
                correct_loss = (
                    float(compiler["policy_kl_weight"]) * policy_kl
                    + float(compiler["teacher_token_ce_weight"])
                    * policy_terms["teacher_token_ce"]
                    + float(compiler["ground_truth_ce_weight"]) * gt_ce
                    + float(compiler["ratio_restraint_weight"])
                    * (
                        F.relu(correct_ratio["raw_layer_ratio"] - 1.0)
                        .pow(2)
                        .mean()
                        + 0.01 * latent.pow(2).mean()
                    )
                )
                correct_loss.backward()

            control = mismatch[pair_id]
            mismatch_state = state
            mismatch_transition = transition
            if mismatch_type == "transition":
                mismatch_transition = representations["transition_values"][
                    transition_position[str(control["transition_mismatch_transition_id"])]
                ].unsqueeze(0).to(backend.device)
            else:
                mismatch_state = representations["state_values"][
                    state_position[str(control["state_mismatch_state_example_id"])]
                ].unsqueeze(0).to(backend.device)
            wrong_latent, wrong_delta, wrong_ratio = _program_delta(
                model=model,
                decoder=decoder,
                state=mismatch_state,
                transition=mismatch_transition,
                base=base,
                maximum_ratio=float(compiler["ratio_budget_per_layer"]),
            )
            mismatch_batch = _collate(
                [direct_row], device=backend.device, k=K_TOKENS
            )
            with DeepResidualHooks(
                model=backend.model,
                layer_indices=LAYER_INDICES,
                selected_token_indices=_selected_indices(mismatch_batch),
                delta=wrong_delta,
                expected_prefill_length=int(mismatch_batch["input_ids"].shape[1]),
            ):
                mismatch_nll, mismatch_logits = _bare_target_forward(
                    backend=backend, batch=mismatch_batch
                )
                mismatch_kl, mismatch_terms = _policy_loss(
                    mismatch_logits, data["bare_teachers"][pair_id]
                )
                mismatch_utility = (
                    float(direct_row["response_cache"]["baseline_mean_target_nll"])
                    - mismatch_nll
                )
                mismatch_loss = (
                    float(compiler["mismatch_policy_kl_weight"]) * mismatch_kl
                    + float(compiler["mismatch_token_ce_weight"])
                    * mismatch_terms["teacher_token_ce"]
                    + float(compiler["mismatch_utility_weight"])
                    * F.smooth_l1_loss(
                        mismatch_utility,
                        torch.zeros_like(mismatch_utility),
                        beta=float(compiler["sequence_huber_delta"]),
                    )
                    + float(compiler["ratio_restraint_weight"])
                    * (
                        F.relu(wrong_ratio["raw_layer_ratio"] - 1.0)
                        .pow(2)
                        .mean()
                        + 0.01 * wrong_latent.pow(2).mean()
                    )
                )
                mismatch_loss.backward()
            torch.nn.utils.clip_grad_norm_(
                list(model.parameters()) + list(decoder.parameters()),
                float(compiler["max_grad_norm"]),
            )
            optimizer.step()
            update_counts[index] += 1
            maximum_ratio = max(
                float(correct_ratio["maximum_ratio"].detach().cpu()),
                float(wrong_ratio["maximum_ratio"].detach().cpu()),
            )
            if maximum_ratio > float(compiler["ratio_budget_per_layer"]) + 1.0e-4:
                raise RuntimeError("Residual ratio exceeded 1.0")
            total_loss = correct_loss + mismatch_loss
            if not math.isfinite(float(total_loss.detach().cpu())):
                raise RuntimeError("Training loss is non-finite")
            metrics["correct_policy_kl"].append(float(policy_kl.detach().cpu()))
            metrics["correct_teacher_token_ce"].append(
                float(policy_terms["teacher_token_ce"].detach().cpu())
            )
            metrics["correct_ground_truth_ce"].append(float(gt_ce.detach().cpu()))
            metrics["mismatch_policy_kl"].append(float(mismatch_kl.detach().cpu()))
            metrics["mismatch_teacher_token_ce"].append(
                float(mismatch_terms["teacher_token_ce"].detach().cpu())
            )
            metrics["mismatch_utility_abs"].append(
                abs(float(mismatch_utility.detach().cpu()))
            )
            metrics["loss"].append(float(total_loss.detach().cpu()))
            metrics["maximum_ratio"].append(maximum_ratio)

        accounting = update_count_summary(pair_ids, update_counts)
        if not accounting["all_pairs_equal"] or int(
            accounting["minimum_updates_per_pair"]
        ) != update_round:
            raise RuntimeError("Training update accounting differs")
        if update_round not in CHECKPOINT_UPDATES:
            continue
        validation_rows = _evaluation_rows(
            model=model,
            decoder=decoder,
            pair_rows=validation_pairs,
            mismatch_manifest=_json(
                paths["evaluation_mismatch_root"] / "A_validation.json"
            ),
            data=data,
            representations=representations,
            base_states=base_states,
            backend=backend,
            settings=settings,
        )
        validation_summary = _summarize_evaluation(
            validation_rows, float(compiler["sequence_huber_delta"])
        )
        validation_path = paths["root"] / f"a_validation_u{update_round:02d}.jsonl"
        write_jsonl(validation_path, validation_rows)
        entry = {
            "updates_per_pair": update_round,
            "mismatch_type_for_round": mismatch_type,
            "training_metrics": {
                name: statistics.fmean(values) for name, values in metrics.items()
            },
            "maximum_ratio": max(metrics["maximum_ratio"]),
            "a_validation": validation_summary,
            "a_validation_rows_path": str(validation_path),
            "a_validation_rows_sha256": sha256_file(validation_path),
            "update_accounting": accounting,
            "elapsed_seconds": time.perf_counter() - started,
        }
        history.append(entry)
        checkpoint = paths["root"] / f"checkpoints/model_u{update_round:02d}.pt"
        atomic_torch_save(
            _checkpoint_payload(
                model=model,
                decoder=decoder,
                optimizer=optimizer,
                pair_ids=pair_ids,
                update_counts=update_counts,
                completed_rounds=update_round,
                history=history,
                pair_contract_sha256=sha256_file(paths["pair_contract"]),
                mismatch_manifest_sha256=sha256_file(paths["training_mismatches"]),
            ),
            checkpoint,
        )
        atomic_write_json(
            paths["latest_checkpoint"],
            {"checkpoint": str(checkpoint), "updates_per_pair": update_round},
        )
        attempt.progress(
            status=f"memory_specific_pairmlp_u{update_round}",
            updates_per_pair=update_round,
            latest_validated_checkpoint=str(checkpoint),
        )
        print(
            f"memory-specific PairMLP u{update_round} "
            f"correct_kl={validation_summary['correct_raw_policy_kl']:.6f} "
            f"transition_bare_kl={validation_summary['transition_mismatch_bare_policy_kl']:.6f} "
            f"state_bare_kl={validation_summary['state_mismatch_bare_policy_kl']:.6f}",
            flush=True,
        )

    if not history or int(history[-1]["updates_per_pair"]) != 8:
        raise RuntimeError("Training did not reach the preregistered u8 checkpoint")
    selected = select_checkpoint(history)
    selected_path = paths["root"] / (
        f"checkpoints/model_u{int(selected['updates_per_pair']):02d}.pt"
    )
    summary = {
        "format": "memory_specific_deep_pairmlp_training_summary_7g_v1",
        "global_seed": GLOBAL_SEED,
        "train_pair_count": len(train_pairs),
        "train_task_count": len({str(row["state_task_id"]) for row in train_pairs}),
        "validation_pair_count": len(validation_pairs),
        "history": history,
        "selected_updates_per_pair": int(selected["updates_per_pair"]),
        "selected_checkpoint": str(selected_path),
        "selected_checkpoint_sha256": sha256_file(selected_path),
        "selected_A_validation": selected["a_validation"],
        "selection_diagnostics": selected["selection_diagnostics"],
        "selection_constraints_passed": bool(
            selected["selection_constraints_passed"]
        ),
        "selection_rule": str(selected["selection_rule"]),
        "model_sha256": str(
            torch.load(selected_path, map_location="cpu", weights_only=False)[
                "model_sha256"
            ]
        ),
        "decoder_sha256": str(
            torch.load(selected_path, map_location="cpu", weights_only=False)[
                "decoder_sha256"
            ]
        ),
        "qwen_frozen": not any(
            parameter.requires_grad for parameter in backend.model.parameters()
        ),
        "selector_sha256": sha256_file(paths["selector"]),
        "student_prompt_contains_raw_transition": False,
        "elapsed_seconds": time.perf_counter() - started,
        "passed": bool(selected["selection_constraints_passed"]),
    }
    atomic_write_json(paths["training_summary"], summary)
    return summary


def _load_selected(
    *,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    representations: Mapping[str, Any],
    device: torch.device,
) -> tuple[nn.Module, nn.Module, dict[str, Any]]:
    summary = _json(paths["training_summary"])
    checkpoint = Path(str(summary["selected_checkpoint"]))
    if sha256_file(checkpoint) != str(summary["selected_checkpoint_sha256"]):
        raise ValueError("Selected PairMLP checkpoint hash changed")
    payload = torch.load(checkpoint, map_location=device, weights_only=False)
    model = _build_model(
        kind="pairmlp",
        settings=settings,
        view_names=representations["transition_view_names"],
        device=device,
    )
    decoder = _build_decoder(settings, 4096, device)
    model.load_state_dict(payload["model_state_dict"])
    decoder.load_state_dict(payload["decoder_state_dict"])
    model.eval()
    decoder.eval()
    return model, decoder, summary


def _evaluate_final(
    *,
    backend: Any,
    cfg: Any,
    settings: Mapping[str, Any],
    artifact_dir: Path,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    paths = _paths(settings, artifact_dir)
    _require(paths, ("training_summary", "raw_teacher_summary", "pair_contract"))
    data = _load_data(backend=backend, cfg=cfg, settings=settings, artifact_dir=artifact_dir)
    contract = _json(paths["pair_contract"])
    representations = _load_representations(paths, backend.device)
    base_states = _base_states(
        backend=backend,
        rows=list(data["direct"].values()),
        path=paths["parent_base_states"],
    )
    model, decoder, training = _load_selected(
        settings=settings,
        paths=paths,
        representations=representations,
        device=backend.device,
    )
    sets = {
        "A_validation": contract["validation_pair_ids"],
        **contract["cell_pair_ids"],
    }
    results = {}
    for cell, pair_ids in sets.items():
        rows = _evaluation_rows(
            model=model,
            decoder=decoder,
            pair_rows=_pair_rows_from_ids(pair_ids, data["pairs"]),
            mismatch_manifest=_json(paths["evaluation_mismatch_root"] / f"{cell}.json"),
            data=data,
            representations=representations,
            base_states=base_states,
            backend=backend,
            settings=settings,
        )
        path = paths["root"] / f"evaluation/{cell}/rows.jsonl"
        write_jsonl(path, rows)
        results[cell] = {
            **_summarize_evaluation(
                rows, float(settings["compiler"]["sequence_huber_delta"])
            ),
            "rows_path": str(path),
            "rows_sha256": sha256_file(path),
        }
        attempt.progress(
            status="memory_specific_final_evaluation",
            completed_cell=cell,
            latest_validated_checkpoint=str(path),
        )
        print(
            f"memory-specific eval {cell} correct_kl={results[cell]['correct_raw_policy_kl']:.6f}",
            flush=True,
        )
    finite = all(bool(value["finite"]) for value in results.values())
    summary = {
        "format": "memory_specific_deep_pairmlp_final_evaluation_7g_v1",
        "global_seed": GLOBAL_SEED,
        "selected_checkpoint_sha256": str(training["selected_checkpoint_sha256"]),
        "selected_updates_per_pair": int(training["selected_updates_per_pair"]),
        "cells": results,
        "B_C_D_E_used_for_checkpoint_selection": False,
        "policy_metrics_primary": True,
        "sequence_utility_metrics_diagnostic": True,
        "qwen_frozen": True,
        "selector_unchanged": sha256_file(paths["selector"])
        == str(settings["expected_selector_sha256"]),
        "student_prompt_contains_raw_transition": False,
        "finite": finite,
        "one_step_authorized": finite,
        "passed": finite,
    }
    atomic_write_json(paths["evaluation_summary"], summary)
    atomic_write_text(
        paths["root"] / "teacher_forced_report.md",
        "\n".join(
            [
                "# EXP-027B memory-specific PairMLP teacher-forced report",
                "",
                f"- selected checkpoint: `u{training['selected_updates_per_pair']}`",
                f"- checkpoint SHA256: `{training['selected_checkpoint_sha256']}`",
                *[
                    f"- {cell}: correct/raw zero KL `{value['correct_raw_policy_kl']:.6f}/{value['zero_raw_policy_kl']:.6f}`, "
                    f"transition/state mismatch-to-bare KL `{value['transition_mismatch_bare_policy_kl']:.6f}/{value['state_mismatch_bare_policy_kl']:.6f}`, "
                    f"utility Spearman `{value['correct_sequence_utility_spearman']}`"
                    for cell, value in results.items()
                ],
                "",
            ]
        ),
    )
    return summary


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    replay_cfg = load_config(args.replay_config)
    settings = cfg.raw["stage_c_7g"]
    replay = replay_cfg.raw["stage_c_7b"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-027B requires seed 25101")
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    seed_everything(GLOBAL_SEED)
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    paths = _paths(settings, args.artifact_dir)
    input_hashes = {
        "config": sha256_file(args.config),
        "replay_config": sha256_file(args.replay_config),
        "selector": sha256_file(paths["selector"]),
        "replay_lineage": sha256_file(paths["replay_lineage"]),
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"phase_b_{args.phase}",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=input_hashes["config"],
        data_manifest_hashes=input_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "preflight":
            result = _preflight(cfg=cfg, settings=settings, artifact_dir=args.artifact_dir)
        elif args.phase == "teacher":
            result = _teacher_cache(
                cfg=cfg,
                settings=settings,
                replay=replay,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
            )
        else:
            backend = _build_backend_from_generation(replay["causal_audit"]["generation"])
            if args.phase == "train":
                result = _train(
                    backend=backend,
                    cfg=cfg,
                    settings=settings,
                    artifact_dir=args.artifact_dir,
                    attempt=attempt,
                )
            else:
                result = _evaluate_final(
                    backend=backend,
                    cfg=cfg,
                    settings=settings,
                    artifact_dir=args.artifact_dir,
                    attempt=attempt,
                )
        print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
