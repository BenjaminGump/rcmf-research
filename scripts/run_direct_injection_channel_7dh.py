from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import random
import statistics
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import nn

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.config import load_config
from rcmf.model.backends.hf_qwen import HFQwenBackend
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.direct_injection_channel_7dh import (
    CHANNEL_CONDITIONS,
    GLOBAL_SEED,
    K_VALUES,
    DirectDeltaInjector,
    build_channel_pair_manifest,
    channel_gate,
    continuation_decision,
    require_global_seed,
    runtime_projection,
)
from rcmf.training.oracle_capacity_5e import project_delta_slots_to_ratio_
from rcmf.training.oracle_convergence_5fa import atomic_torch_save, update_count_summary
from rcmf.training.procedural_causal_analysis_7b import (
    comparison_set,
    condition_summary,
    per_task_summary,
)
from rcmf.training.procedural_causal_audit_6h import evaluate_generated_action
from rcmf.training.procedural_causal_audit_7b import (
    LiveBridgeClient,
    build_live_appworld_messages,
    condition_checkpoint_name,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256, stable_key
from rcmf.training.state_conditioned_program_direct_7dg import seed_everything
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    maybe_git_commit,
    read_jsonl,
    sha256_file,
    sha256_text,
)
from scripts.prepare_state_conditioned_program_7d import _context_builder, _preflight_pair
from scripts.run_procedural_causal_audit_7b import (
    _examples_by_state,
    _prepare_message,
    _records_by_task,
    _state_contract,
)
from scripts.run_stage_c_oracle_capacity_5e import (
    _collate,
    _forward_direct_delta,
    _precompute_direct_base_norms,
)
from scripts.run_state_conditioned_program_fast_one_step_7df import (
    _load_parent_rows,
)
from scripts.run_state_conditioned_program_policy_distill_7dg3 import (
    _build_backend_from_generation,
    _policy_loss,
    _policy_tokenized_row,
    _teacher_policy_row,
    _validate_teacher_row,
)


RESULT_FORMAT = "direct_injection_channel_one_step_result_7dh_v1"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_direct_injection_channel_7dh.yaml"),
    )
    parser.add_argument(
        "--replay-config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--phase",
        choices=(
            "preflight",
            "teacher",
            "train",
            "teacher_forced",
            "one_step_preflight",
            "one_step",
            "analyze",
        ),
        required=True,
    )
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp026a")
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _row_path(root: Path, pair_id: str) -> Path:
    return root / f"{sha256_text(pair_id)}.json"


def _paths(
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    run: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Path]:
    parent_g3 = Path(str(run["parent_g3"]))
    parent_direct = Path(str(g3["parent_direct"]))
    parent_b = Path(str(direct["parent_exp025b"]))
    parent_c = Path(str(direct["parent_exp025c"]))
    parent_cr = Path(str(direct["parent_exp025cr"]))
    corpus = Path(str(direct["reconciled_corpus_dir"]))
    return {
        "parent_g3_analysis": parent_g3 / "one_step/analysis.json",
        "parent_policy_analysis": parent_g3 / "policy_distillation/one_step/analysis.json",
        "parent_g3_manifest": parent_g3 / "one_step/condition_manifest.json",
        "parent_policy_teacher_rows": parent_g3 / "policy_distillation/teacher_cache/rows",
        "direct_teacher_rows": parent_direct / "teacher_cache/rows",
        "pairs_E": parent_direct / "preflight/pairs_E.jsonl",
        "selector": parent_c / "selector/ensemble_scores.pt",
        "parent_c0_outputs": parent_b / "condition_outputs",
        "parent_f3_outputs": parent_cr / "selector_condition_outputs",
        "replay_lineage": parent_b / "replay_validated_corpus_manifest.json",
        "decisions": corpus / "decision_examples.jsonl",
        "memories": corpus / "memory_records.jsonl",
        "transitions": parent_b
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        "semantic_module": Path("rcmf/training/appworld_replay_clean_rebuild_7b.py"),
        "bridge_script": Path("scripts/appworld_live_one_step_bridge_7b.py"),
        "manifest": artifact_dir / "pair_manifest.json",
        "preflight": artifact_dir / "preflight.json",
        "teacher_rows": artifact_dir / "teacher_cache/rows",
        "teacher_summary": artifact_dir / "teacher_cache/summary.json",
        "training_summary": artifact_dir / "training/summary.json",
        "teacher_forced": artifact_dir / "teacher_forced/summary.json",
        "condition_manifest": artifact_dir / "one_step/condition_manifest.json",
        "one_step_preflight": artifact_dir / "one_step/preflight.json",
        "generation": artifact_dir / "one_step/generation_summary.json",
        "analysis": artifact_dir / "one_step/analysis.json",
    }


def _require_paths(paths: Mapping[str, Path], names: Sequence[str]) -> None:
    missing = {name: str(paths[name]) for name in names if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing EXP-026A input paths: {missing}")


def _teacher_path(paths: Mapping[str, Path], pair_id: str) -> tuple[Path, bool]:
    parent = _row_path(paths["parent_policy_teacher_rows"], pair_id)
    if parent.exists():
        return parent, True
    return _row_path(paths["teacher_rows"], pair_id), False


def _preflight(
    *,
    cfg: Any,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    policy: Mapping[str, Any],
    run: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    paths = _paths(direct, g3, run, artifact_dir)
    required = (
        "parent_g3_analysis",
        "parent_policy_analysis",
        "parent_g3_manifest",
        "pairs_E",
        "selector",
        "parent_c0_outputs",
        "parent_f3_outputs",
        "replay_lineage",
        "decisions",
        "memories",
        "transitions",
        "semantic_module",
        "bridge_script",
    )
    _require_paths(paths, required)
    require_global_seed(int(run["global_seed"]))
    parent_analysis = _json(paths["parent_g3_analysis"])
    parent_policy = _json(paths["parent_policy_analysis"])
    checks = {
        "parent_g3_analysis_hash": sha256_file(paths["parent_g3_analysis"])
        == str(run["expected_parent_g3_analysis_sha256"]),
        "parent_policy_analysis_hash": sha256_file(paths["parent_policy_analysis"])
        == str(run["expected_parent_g3_policy_analysis_sha256"]),
        "parent_branch": str(parent_analysis["decision_branch"])
        == str(run["expected_parent_branch"]),
        "parent_pairmlp_failed": not bool(parent_analysis["pairmlp_one_step_behavior_passed"]),
        "parent_policy_failed": not bool(parent_policy["policy_pairmlp_one_step_passed"]),
        "selector": sha256_file(paths["selector"]) == str(run["expected_selector_sha256"]),
        "replay_lineage": str(_json(paths["replay_lineage"])["lineage_sha256"])
        == str(run["expected_replay_lineage_sha256"]),
        "k_values": tuple(int(value) for value in run["channel"]["k_values"])
        == K_VALUES,
        "position": str(run["channel"]["position"]) == "last_user_k",
    }
    if not all(checks.values()):
        raise ValueError(f"EXP-026A immutable input validation failed: {checks}")

    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        str(run["expected_model_name"]), trust_remote_code=True
    )
    if tokenizer.pad_token_id is None and tokenizer.eos_token is not None:
        tokenizer.pad_token = tokenizer.eos_token
    examples = load_decision_examples(paths["decisions"])
    contexts, _ = _context_builder(
        tokenizer=tokenizer,
        examples=examples,
        prompt_profile=cfg.benchmark.prompt_profile,
    )
    transitions = {str(row["transition_id"]): row for row in _rows(paths["transitions"])}
    conditions = _json(paths["parent_g3_manifest"])["conditions"]
    e_pairs = _rows(paths["pairs_E"])
    e_by_id = {str(row["pair_id"]): row for row in e_pairs}
    last_user_counts = {}
    cached = set()
    selected_conditions = [
        row
        for row in conditions
        if str(row.get("condition_name")) == "P1_pairmlp_correct"
        and str(row.get("audit_stratum")) in {"A", "B"}
    ]
    candidate_pairs = []
    context_cache: dict[tuple[str, str], dict[str, Any]] = {}
    for condition in sorted(selected_conditions, key=lambda row: str(row["state_example_id"])):
        state_id = str(condition["state_example_id"])
        transition_id = str(condition["program_transition_id"])
        pair_id = f"{state_id}::transition::{transition_id}"
        transition = transitions[transition_id]
        source = e_by_id.get(pair_id)
        base = dict(source) if source is not None else {
            "pair_id": pair_id,
            "cell": "E",
            "pair_role": "frozen_selector_top_class",
            "selection_rule": "frozen_exp025cr_deployment_e",
            "selection_uses_heldout_labels": False,
            "state_example_id": state_id,
            "state_task_id": str(condition["state_task_id"]),
            "transition_id": transition_id,
            "transition_parent_id": str(transition["parent_memory_id"]),
            "transition_parent_task_id": str(transition["parent_task_id"]),
            "signature_class_id": str(condition["signature_class_id"]),
            "pair_metadata_source": "reconstructed_from_frozen_condition_and_clean_manifests",
        }
        pair = _preflight_pair(
            row=base,
            tokenizer=tokenizer,
            contexts=contexts,
            transitions=transitions,
            prompt_profile=cfg.benchmark.prompt_profile,
            context_limit=int(run["teacher"]["context_limit"]),
            cache=context_cache,
        )
        pair["score_status"] = "over_context" if pair["over_context"] else "scoreable"
        pair["valid_for_teacher_cache"] = not bool(pair["over_context"])
        candidate_pairs.append(pair)
        last_user_counts[pair_id] = len(
            contexts[state_id]["prompt_metadata"].get("last_user_token_indices", [])
        )
        parent_teacher = _row_path(paths["parent_policy_teacher_rows"], pair_id)
        if parent_teacher.exists():
            cached.add(pair_id)
    manifest = build_channel_pair_manifest(
        conditions=conditions,
        e_pairs=candidate_pairs,
        last_user_counts=last_user_counts,
        cached_teacher_pair_ids=cached,
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(paths["manifest"], manifest)

    feasible = {
        str(k): int(manifest["feasibility"][str(k)]["feasible_count"])
        for k in K_VALUES
    }
    runtime = runtime_projection(
        feasible_counts=feasible,
        new_teacher_count=int(manifest["new_teacher_count"]),
        rates=run["runtime"]["rates"],
        maximum_updates_per_pair=int(run["channel"]["maximum_updates_per_pair"]),
    )
    expected = float(runtime["scenarios"]["expected"]["maximum_h100_hours"])
    threshold = float(run["runtime"]["review_threshold_h100_hours"])
    launch = expected <= threshold
    model_dim = 4096
    parameter_counts = {
        str(k): {
            "per_pair": int(k) * model_dim,
            "all_feasible_pairs": int(k) * model_dim * feasible[str(k)],
        }
        for k in K_VALUES
    }
    projected_bytes = (
        sum(value["all_feasible_pairs"] for value in parameter_counts.values())
        * int(run["runtime"]["projected_bytes_per_delta_parameter"])
        * 4
        + (int(manifest["new_teacher_count"]) * int(run["runtime"]["projected_bytes_per_teacher_row"]))
        + runtime["one_step_generation_count"]
        * int(run["runtime"]["projected_bytes_per_condition"])
    )
    report = {
        "format": "direct_injection_channel_preflight_7dh_v1",
        "run_uuid": str(run["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "immutable_checks": checks,
        "pair_count": int(manifest["pair_count"]),
        "task_count": int(manifest["task_count"]),
        "feasibility": manifest["feasibility"],
        "reused_teacher_rows": int(manifest["cached_teacher_count"]),
        "new_teacher_rows": int(manifest["new_teacher_count"]),
        "parameter_counts": parameter_counts,
        "runtime": runtime,
        "projected_artifact_bytes": projected_bytes,
        "review_threshold_h100_hours": threshold,
        "automatic_launch_allowed": launch,
        "student_prompt_contains_raw_transition": False,
        "shared_decoder_used": False,
        "latent_128_used": False,
        "passed": launch,
    }
    atomic_write_json(paths["preflight"], report)
    lines = [
        "# EXP-026A Direct Injection Channel Runtime Preflight",
        "",
        f"- run UUID: `{run['run_uuid']}`",
        f"- global seed: `{GLOBAL_SEED}`",
        f"- primary pairs/tasks: `{report['pair_count']}/{report['task_count']}`",
        f"- teacher rows reused/new: `{report['reused_teacher_rows']}/{report['new_teacher_rows']}`",
        f"- K feasibility: `{feasible}`",
        f"- optimizer backward calls min/max: `{runtime['optimizer_backward_calls_minimum']}/{runtime['optimizer_backward_calls_maximum']}`",
        f"- Qwen backward-path equivalents min/max: `{runtime['qwen_backward_path_equivalents_minimum']}/{runtime['qwen_backward_path_equivalents_maximum']}`",
        f"- one-step generations/executions: `{runtime['one_step_generation_count']}`",
        f"- expected maximum H100 hours: `{expected:.4f}`",
        f"- conservative maximum H100 hours: `{runtime['scenarios']['conservative']['maximum_h100_hours']:.4f}`",
        f"- projected artifact bytes: `{projected_bytes}`",
        f"- automatic launch under 6-hour gate: `{str(launch).lower()}`",
        "- direct DeltaE only; no decoder, latent, PairMLP, factorized program, or selector training",
        "",
    ]
    atomic_write_text(artifact_dir / "runtime_preflight.md", "\n".join(lines))
    return report


def _teacher_cache(
    *,
    cfg: Any,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    policy: Mapping[str, Any],
    run: Mapping[str, Any],
    replay: Mapping[str, Any],
    artifact_dir: Path,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    paths = _paths(direct, g3, run, artifact_dir)
    preflight = _json(paths["preflight"])
    if not bool(preflight["automatic_launch_allowed"]):
        raise RuntimeError("EXP-026A runtime preflight did not authorize GPU work")
    manifest = _json(paths["manifest"])
    backend = _build_backend_from_generation(replay["causal_audit"]["generation"])
    examples = load_decision_examples(paths["decisions"])
    contexts, _ = _context_builder(
        tokenizer=backend.tokenizer,
        examples=examples,
        prompt_profile=cfg.benchmark.prompt_profile,
    )
    transitions = {str(row["transition_id"]): row for row in _rows(paths["transitions"])}
    paths["teacher_rows"].mkdir(parents=True, exist_ok=True)
    reused = 0
    created = 0
    started = time.perf_counter()
    row_hashes = {}
    for ordinal, pair in enumerate(manifest["pairs"], start=1):
        output, parent = _teacher_path(paths, str(pair["pair_id"]))
        if output.exists():
            row = _json(output)
            _validate_teacher_row(row, pair, policy)
            reused += int(parent)
        else:
            row = _teacher_policy_row(
                backend=backend,
                context=contexts[str(pair["state_example_id"])],
                pair=pair,
                transition=transitions[str(pair["transition_id"])],
                prompt_profile=cfg.benchmark.prompt_profile,
                teacher_settings=run["teacher"],
                structural_lineage=str(g3["expected_structural_lineage_sha256"]),
            )
            atomic_write_json(output, row)
            created += 1
        row_hashes[str(pair["pair_id"])] = sha256_file(output)
        attempt.progress(
            status="direct_channel_teacher_cache",
            completed_pairs=ordinal,
            total_pairs=len(manifest["pairs"]),
            latest_validated_checkpoint=str(output),
        )
        print(f"direct channel teacher {ordinal}/{len(manifest['pairs'])}", flush=True)
    summary = {
        "format": "direct_injection_channel_teacher_cache_7dh_v1",
        "pair_count": len(row_hashes),
        "reused_rows": reused,
        "new_rows": created,
        "row_set_sha256": canonical_sha256(row_hashes),
        "elapsed_seconds": time.perf_counter() - started,
        "qwen_frozen": True,
        "passed": len(row_hashes) == 32 and created == int(manifest["new_teacher_count"]),
    }
    atomic_write_json(paths["teacher_summary"], summary)
    return summary


def _ground_truth_tokenized_row(
    *,
    backend: HFQwenBackend,
    context: Mapping[str, Any],
    pair: Mapping[str, Any],
    context_limit: int,
) -> dict[str, Any]:
    tokenized = backend.tokenize_messages(
        context["base_messages"], add_generation_prompt=True
    )
    prompt_ids = [int(value) for value in tokenized.input_ids[0].cpu().tolist()]
    target_ids = [int(value) for value in context["target_ids"]]
    full_ids = prompt_ids + target_ids
    if len(full_ids) > int(context_limit):
        raise ValueError(f"Ground-truth row exceeds context: {pair['pair_id']}")
    rendered = str(tokenized.metadata["text"])
    if sha256_text(rendered) != str(pair["prompt_sha256"]):
        raise ValueError(f"Bare ground-truth prompt differs for {pair['pair_id']}")
    return {
        "pair_id": str(pair["pair_id"]),
        "state_example_id": str(pair["state_example_id"]),
        "state_task_id": str(pair["state_task_id"]),
        "transition_id": str(pair["transition_id"]),
        "transition_parent_id": str(pair["transition_parent_id"]),
        "cell": str(pair["cell"]),
        "input_ids": full_ids,
        "labels": [-100] * len(prompt_ids) + target_ids,
        "pad_token_id": int(backend.tokenizer.pad_token_id),
        "last_user_token_indices": [
            int(value) for value in tokenized.metadata["last_user_token_indices"]
        ],
        "target_len": len(target_ids),
        "response_cache": {
            "target_sha256": str(context["target_sha256"]),
            "target_token_sha256": str(context["target_token_sha256"]),
        },
        "student_prompt_sha256": str(pair["prompt_sha256"]),
        "student_prompt_contains_raw_transition": False,
    }


def _load_training_data(
    *,
    backend: HFQwenBackend,
    cfg: Any,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    run: Mapping[str, Any],
    artifact_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    paths = _paths(direct, g3, run, artifact_dir)
    manifest = _json(paths["manifest"])
    examples = load_decision_examples(paths["decisions"])
    contexts, _ = _context_builder(
        tokenizer=backend.tokenizer,
        examples=examples,
        prompt_profile=cfg.benchmark.prompt_profile,
    )
    teachers = {}
    for pair in manifest["pairs"]:
        path, _ = _teacher_path(paths, str(pair["pair_id"]))
        teachers[str(pair["pair_id"])] = _json(path)
    policy_rows = [
        _policy_tokenized_row(
            backend=backend,
            context=contexts[str(pair["state_example_id"])],
            pair=pair,
            teacher=teachers[str(pair["pair_id"])],
        )
        for pair in manifest["pairs"]
    ]
    ground_truth = [
        _ground_truth_tokenized_row(
            backend=backend,
            context=contexts[str(pair["state_example_id"])],
            pair=pair,
            context_limit=int(direct["teacher_cache"]["context_limit"]),
        )
        for pair in manifest["pairs"]
    ]
    gt_by_id = {str(row["pair_id"]): row for row in ground_truth}
    ground_truth_rows = [gt_by_id[str(row["pair_id"])] for row in policy_rows]
    return manifest, policy_rows, ground_truth_rows, [teachers[str(row["pair_id"])] for row in policy_rows]


def _evaluate_delta(
    *,
    backend: HFQwenBackend,
    policy_rows: Sequence[dict[str, Any]],
    ground_truth_rows: Sequence[dict[str, Any]],
    teachers: Sequence[dict[str, Any]],
    deltas: Sequence[torch.Tensor],
    k: int,
) -> dict[str, Any]:
    rows = []
    with torch.no_grad():
        for index, (policy_row, gt_row, teacher) in enumerate(
            zip(policy_rows, ground_truth_rows, teachers, strict=True)
        ):
            delta = deltas[index].to(backend.device).unsqueeze(0)
            policy_batch = _collate([policy_row], device=backend.device, k=k)
            student = _forward_direct_delta(
                backend=backend, batch=policy_batch, delta_slots=delta
            )
            kl, terms = _policy_loss(student["target_logits"], teacher)
            gt_batch = _collate([gt_row], device=backend.device, k=k)
            gt = _forward_direct_delta(
                backend=backend, batch=gt_batch, delta_slots=delta
            )
            rows.append(
                {
                    "pair_id": str(policy_row["pair_id"]),
                    "state_example_id": str(policy_row["state_example_id"]),
                    "state_task_id": str(policy_row["state_task_id"]),
                    "transition_id": str(policy_row["transition_id"]),
                    "teacher_policy_kl": float(kl.cpu()),
                    "teacher_token_ce": float(terms["teacher_token_ce"].cpu()),
                    "teacher_token_top1_accuracy": float(terms["top1"].cpu()),
                    "ground_truth_target_nll": float(gt["loss"].cpu()),
                    "delta_ratio": float(student["delta_ratio"].cpu()),
                    "delta_norm": float(student["delta_norm"].cpu()),
                    "base_norm": float(student["base_norm"].cpu()),
                    "selected_token_indices": student["selected_token_indices"][0],
                }
            )
    return {
        "row_count": len(rows),
        "teacher_policy_kl": statistics.fmean(row["teacher_policy_kl"] for row in rows),
        "teacher_token_ce": statistics.fmean(row["teacher_token_ce"] for row in rows),
        "teacher_token_top1_accuracy": statistics.fmean(
            row["teacher_token_top1_accuracy"] for row in rows
        ),
        "ground_truth_target_nll": statistics.fmean(
            row["ground_truth_target_nll"] for row in rows
        ),
        "delta_ratio_mean": statistics.fmean(row["delta_ratio"] for row in rows),
        "delta_ratio_max": max(row["delta_ratio"] for row in rows),
        "rows": rows,
    }


def _checkpoint_payload(
    *,
    k: int,
    params: nn.ParameterList,
    optimizer: torch.optim.Optimizer,
    pair_ids: Sequence[str],
    update_counts: Sequence[int],
    completed_rounds: int,
    curve: Sequence[Mapping[str, Any]],
    manifest_sha256: str,
) -> dict[str, Any]:
    return {
        "format": "direct_injection_channel_checkpoint_7dh_v1",
        "global_seed": GLOBAL_SEED,
        "k": int(k),
        "model_dim": int(params[0].shape[-1]),
        "pair_ids": list(pair_ids),
        "deltas": torch.stack([value.detach().cpu() for value in params]),
        "optimizer_state_dict": optimizer.state_dict(),
        "update_counts": [int(value) for value in update_counts],
        "update_accounting": update_count_summary(pair_ids, update_counts),
        "completed_rounds": int(completed_rounds),
        "curve": list(curve),
        "manifest_sha256": str(manifest_sha256),
        "source_commit": maybe_git_commit(),
        "python_random_state": random.getstate(),
        "torch_rng_state": torch.get_rng_state(),
        "cuda_rng_state": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
    }


def _train_one_k(
    *,
    backend: HFQwenBackend,
    run: Mapping[str, Any],
    manifest: Mapping[str, Any],
    policy_rows_all: Sequence[dict[str, Any]],
    ground_truth_all: Sequence[dict[str, Any]],
    teachers_all: Sequence[dict[str, Any]],
    k: int,
    artifact_dir: Path,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    feasible = [
        index
        for index, row in enumerate(manifest["pairs"])
        if bool(row["k_feasible"][str(k)])
    ]
    policy_rows = [policy_rows_all[index] for index in feasible]
    ground_truth = [ground_truth_all[index] for index in feasible]
    teachers = [teachers_all[index] for index in feasible]
    pair_ids = [str(row["pair_id"]) for row in policy_rows]
    model_dim = int(backend.model.config.hidden_size)
    seed_everything(GLOBAL_SEED)
    params = nn.ParameterList(
        [
            nn.Parameter(torch.zeros(k, model_dim, dtype=torch.float32, device=backend.device))
            for _ in pair_ids
        ]
    )
    optimizer = torch.optim.AdamW(
        params,
        lr=float(run["channel"]["learning_rate"]),
        weight_decay=0.0,
    )
    base_norms = _precompute_direct_base_norms(
        backend=backend, rows=policy_rows, device=backend.device, k=k
    ).to(backend.device)
    update_counts = [0] * len(pair_ids)
    curve = []
    root = artifact_dir / f"training/K{k}"
    latest = root / "latest.pt"
    completed_rounds = 0
    if latest.exists():
        payload = torch.load(latest, map_location="cpu", weights_only=False)
        checks = {
            "format": str(payload.get("format"))
            == "direct_injection_channel_checkpoint_7dh_v1",
            "seed": int(payload.get("global_seed", -1)) == GLOBAL_SEED,
            "k": int(payload.get("k", -1)) == k,
            "pair_ids": list(payload.get("pair_ids", [])) == pair_ids,
            "manifest": str(payload.get("manifest_sha256"))
            == str(manifest["manifest_sha256"]),
        }
        if not all(checks.values()):
            raise ValueError(f"K{k} resume checkpoint identity differs: {checks}")
        with torch.no_grad():
            for parameter, value in zip(params, payload["deltas"], strict=True):
                parameter.copy_(value.to(backend.device))
        optimizer.load_state_dict(payload["optimizer_state_dict"])
        update_counts = [int(value) for value in payload["update_counts"]]
        curve = list(payload["curve"])
        completed_rounds = int(payload["completed_rounds"])
        random.setstate(payload["python_random_state"])
        torch.set_rng_state(payload["torch_rng_state"].cpu())
        if torch.cuda.is_available() and payload.get("cuda_rng_state"):
            torch.cuda.set_rng_state_all([value.cpu() for value in payload["cuda_rng_state"]])

    if completed_rounds == 0:
        zero = [torch.zeros(k, model_dim) for _ in pair_ids]
        metrics = _evaluate_delta(
            backend=backend,
            policy_rows=policy_rows,
            ground_truth_rows=ground_truth,
            teachers=teachers,
            deltas=zero,
            k=k,
        )
        curve.append({"updates_per_pair": 0, "metrics": metrics})

    settings = run["channel"]
    targets = [4, 8]
    started = time.perf_counter()
    for target in targets:
        if completed_rounds >= target:
            continue
        for round_index in range(completed_rounds + 1, target + 1):
            order = sorted(
                range(len(pair_ids)),
                key=lambda index: stable_key(
                    GLOBAL_SEED, f"direct-channel-k{k}-u{round_index}", pair_ids[index]
                ),
            )
            for index in order:
                delta = params[index].unsqueeze(0)
                policy_batch = _collate(
                    [policy_rows[index]], device=backend.device, k=k
                )
                student = _forward_direct_delta(
                    backend=backend, batch=policy_batch, delta_slots=delta
                )
                kl, terms = _policy_loss(student["target_logits"], teachers[index])
                gt_batch = _collate(
                    [ground_truth[index]], device=backend.device, k=k
                )
                gt = _forward_direct_delta(
                    backend=backend, batch=gt_batch, delta_slots=delta
                )
                loss = (
                    float(settings["policy_kl_weight"]) * kl
                    + float(settings["teacher_token_ce_weight"])
                    * terms["teacher_token_ce"]
                    + float(settings["ground_truth_ce_weight"]) * gt["loss"]
                    + float(settings["ratio_restraint_weight"])
                    * student["delta_ratio"].square()
                )
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(
                    params, float(settings["max_grad_norm"])
                )
                optimizer.step()
                with torch.no_grad():
                    project_delta_slots_to_ratio_(
                        params[index].unsqueeze(0),
                        base_norms[index : index + 1],
                        max_ratio=float(settings["ratio_budget"]),
                    )
                update_counts[index] += 1
            completed_rounds = round_index
            attempt.progress(
                status=f"direct_channel_k{k}_u{round_index}",
                k=k,
                completed_rounds=completed_rounds,
                total_rounds=16,
                completed_pair_updates=sum(update_counts),
            )
        metrics = _evaluate_delta(
            backend=backend,
            policy_rows=policy_rows,
            ground_truth_rows=ground_truth,
            teachers=teachers,
            deltas=[value.detach().cpu() for value in params],
            k=k,
        )
        curve.append({"updates_per_pair": target, "metrics": metrics})
        checkpoint = root / f"checkpoint_u{target:02d}.pt"
        payload = _checkpoint_payload(
            k=k,
            params=params,
            optimizer=optimizer,
            pair_ids=pair_ids,
            update_counts=update_counts,
            completed_rounds=completed_rounds,
            curve=curve,
            manifest_sha256=str(manifest["manifest_sha256"]),
        )
        atomic_torch_save(payload, checkpoint)
        atomic_torch_save(payload, latest)
        attempt.progress(
            status=f"direct_channel_k{k}_checkpoint_u{target}",
            latest_validated_checkpoint=str(checkpoint),
            teacher_policy_kl=float(metrics["teacher_policy_kl"]),
            teacher_token_ce=float(metrics["teacher_token_ce"]),
            ratio_max=float(metrics["delta_ratio_max"]),
        )
        print(
            f"K{k} u{target}: KL={metrics['teacher_policy_kl']:.6f} "
            f"CE={metrics['teacher_token_ce']:.6f} ratio={metrics['delta_ratio_max']:.6f}",
            flush=True,
        )

    by_update = {int(row["updates_per_pair"]): row["metrics"] for row in curve}
    decision = continuation_decision(
        by_update[4],
        by_update[8],
        minimum_relative_improvement=float(settings["continuation_relative_improvement"]),
    )
    if bool(decision["continue_to_u16"]) and completed_rounds < 16:
        for round_index in range(completed_rounds + 1, 17):
            order = sorted(
                range(len(pair_ids)),
                key=lambda index: stable_key(
                    GLOBAL_SEED, f"direct-channel-k{k}-u{round_index}", pair_ids[index]
                ),
            )
            for index in order:
                delta = params[index].unsqueeze(0)
                policy_batch = _collate([policy_rows[index]], device=backend.device, k=k)
                student = _forward_direct_delta(
                    backend=backend, batch=policy_batch, delta_slots=delta
                )
                kl, terms = _policy_loss(student["target_logits"], teachers[index])
                gt_batch = _collate([ground_truth[index]], device=backend.device, k=k)
                gt = _forward_direct_delta(
                    backend=backend, batch=gt_batch, delta_slots=delta
                )
                loss = (
                    float(settings["policy_kl_weight"]) * kl
                    + float(settings["teacher_token_ce_weight"])
                    * terms["teacher_token_ce"]
                    + float(settings["ground_truth_ce_weight"]) * gt["loss"]
                    + float(settings["ratio_restraint_weight"])
                    * student["delta_ratio"].square()
                )
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(params, float(settings["max_grad_norm"]))
                optimizer.step()
                with torch.no_grad():
                    project_delta_slots_to_ratio_(
                        params[index].unsqueeze(0),
                        base_norms[index : index + 1],
                        max_ratio=float(settings["ratio_budget"]),
                    )
                update_counts[index] += 1
            completed_rounds = round_index
            attempt.progress(
                status=f"direct_channel_k{k}_u{round_index}",
                k=k,
                completed_rounds=completed_rounds,
                total_rounds=16,
                completed_pair_updates=sum(update_counts),
            )
        metrics = _evaluate_delta(
            backend=backend,
            policy_rows=policy_rows,
            ground_truth_rows=ground_truth,
            teachers=teachers,
            deltas=[value.detach().cpu() for value in params],
            k=k,
        )
        curve.append({"updates_per_pair": 16, "metrics": metrics})
        checkpoint = root / "checkpoint_u16.pt"
        payload = _checkpoint_payload(
            k=k,
            params=params,
            optimizer=optimizer,
            pair_ids=pair_ids,
            update_counts=update_counts,
            completed_rounds=16,
            curve=curve,
            manifest_sha256=str(manifest["manifest_sha256"]),
        )
        atomic_torch_save(payload, checkpoint)
        atomic_torch_save(payload, latest)
        attempt.progress(
            status=f"direct_channel_k{k}_checkpoint_u16",
            latest_validated_checkpoint=str(checkpoint),
            teacher_policy_kl=float(metrics["teacher_policy_kl"]),
            teacher_token_ce=float(metrics["teacher_token_ce"]),
            ratio_max=float(metrics["delta_ratio_max"]),
        )
        print(
            f"K{k} u16: KL={metrics['teacher_policy_kl']:.6f} "
            f"CE={metrics['teacher_token_ce']:.6f} ratio={metrics['delta_ratio_max']:.6f}",
            flush=True,
        )

    final_updates = 16 if bool(decision["continue_to_u16"]) else 8
    final_path = root / f"checkpoint_u{final_updates:02d}.pt"
    if not final_path.exists():
        raise FileNotFoundError(f"Missing final K{k} checkpoint: {final_path}")
    final_payload = torch.load(final_path, map_location="cpu", weights_only=False)
    final_metrics = next(
        row["metrics"]
        for row in final_payload["curve"]
        if int(row["updates_per_pair"]) == final_updates
    )
    return {
        "k": k,
        "pair_count": len(pair_ids),
        "parameter_count_per_pair": k * model_dim,
        "total_parameter_count": len(pair_ids) * k * model_dim,
        "continuation": decision,
        "final_updates_per_pair": final_updates,
        "final_checkpoint": str(final_path),
        "final_checkpoint_sha256": sha256_file(final_path),
        "curve": final_payload["curve"],
        "final_metrics": final_metrics,
        "elapsed_seconds": time.perf_counter() - started,
        "ratio_budget": float(settings["ratio_budget"]),
        "passed": float(final_metrics["delta_ratio_max"]) <= 1.0001,
    }


def _train(
    *,
    cfg: Any,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    run: Mapping[str, Any],
    replay: Mapping[str, Any],
    artifact_dir: Path,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    paths = _paths(direct, g3, run, artifact_dir)
    if not bool(_json(paths["teacher_summary"])["passed"]):
        raise RuntimeError("EXP-026A teacher cache is incomplete")
    backend = _build_backend_from_generation(replay["causal_audit"]["generation"])
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Qwen became trainable in direct channel audit")
    manifest, policy_rows, ground_truth, teachers = _load_training_data(
        backend=backend,
        cfg=cfg,
        direct=direct,
        g3=g3,
        run=run,
        artifact_dir=artifact_dir,
    )
    started = time.perf_counter()
    results = {}
    for k in K_VALUES:
        results[str(k)] = _train_one_k(
            backend=backend,
            run=run,
            manifest=manifest,
            policy_rows_all=policy_rows,
            ground_truth_all=ground_truth,
            teachers_all=teachers,
            k=k,
            artifact_dir=artifact_dir,
            attempt=attempt,
        )
    summary = {
        "format": "direct_injection_channel_training_summary_7dh_v1",
        "run_uuid": str(run["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "qwen_frozen": True,
        "selector_trained": False,
        "shared_decoder_used": False,
        "latent_128_used": False,
        "pairmlp_trained": False,
        "factorized_program_trained": False,
        "k_results": results,
        "elapsed_seconds": time.perf_counter() - started,
        "passed": all(bool(value["passed"]) for value in results.values()),
    }
    atomic_write_json(paths["training_summary"], summary)
    return summary


def _teacher_forced(
    *,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    run: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    paths = _paths(direct, g3, run, artifact_dir)
    training = _json(paths["training_summary"])
    if not bool(training["passed"]):
        raise RuntimeError("Direct channel training did not complete cleanly")
    cells = {}
    for k in K_VALUES:
        result = training["k_results"][str(k)]
        final = result["final_metrics"]
        zero = next(
            row["metrics"]
            for row in result["curve"]
            if int(row["updates_per_pair"]) == 0
        )
        cells[str(k)] = {
            "k": k,
            "pair_count": int(result["pair_count"]),
            "final_updates_per_pair": int(result["final_updates_per_pair"]),
            "teacher_policy_kl": float(final["teacher_policy_kl"]),
            "zero_policy_kl": float(zero["teacher_policy_kl"]),
            "policy_kl_reduction": float(zero["teacher_policy_kl"])
            - float(final["teacher_policy_kl"]),
            "teacher_token_ce": float(final["teacher_token_ce"]),
            "zero_teacher_token_ce": float(zero["teacher_token_ce"]),
            "teacher_token_ce_reduction": float(zero["teacher_token_ce"])
            - float(final["teacher_token_ce"]),
            "teacher_token_top1_accuracy": float(
                final["teacher_token_top1_accuracy"]
            ),
            "ground_truth_target_nll": float(final["ground_truth_target_nll"]),
            "delta_ratio_mean": float(final["delta_ratio_mean"]),
            "delta_ratio_max": float(final["delta_ratio_max"]),
            "per_pair_rows": final["rows"],
            "checkpoint_sha256": str(result["final_checkpoint_sha256"]),
        }
    summary = {
        "format": "direct_injection_channel_teacher_forced_7dh_v1",
        "k_results": cells,
        "teacher_forced_is_not_scientific_decision": True,
        "passed": all(value["delta_ratio_max"] <= 1.0001 for value in cells.values()),
    }
    atomic_write_json(paths["teacher_forced"], summary)
    lines = ["# EXP-026A Teacher-Forced Capacity", ""]
    for k in K_VALUES:
        value = cells[str(k)]
        lines.append(
            f"- K{k}: KL `{value['teacher_policy_kl']:.6f}` vs zero "
            f"`{value['zero_policy_kl']:.6f}`; top1 "
            f"`{value['teacher_token_top1_accuracy']:.6f}`; GT NLL "
            f"`{value['ground_truth_target_nll']:.6f}`; ratio max "
            f"`{value['delta_ratio_max']:.6f}`"
        )
    lines.extend(["", "Behavioral generation remains the scientific gate.", ""])
    atomic_write_text(artifact_dir / "teacher_forced_report.md", "\n".join(lines))
    return summary


def _one_step_preflight(
    *,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    run: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    paths = _paths(direct, g3, run, artifact_dir)
    if not bool(_json(paths["teacher_forced"])["passed"]):
        raise RuntimeError("Teacher-forced capacity output is invalid")
    manifest = _json(paths["manifest"])
    conditions = []
    for k in K_VALUES:
        controls = manifest["cyclic_controls"][str(k)]
        for pair in manifest["pairs"]:
            if not bool(pair["k_feasible"][str(k)]):
                continue
            pair_id = str(pair["pair_id"])
            for condition_type in CHANNEL_CONDITIONS:
                source_pair = pair_id if condition_type == "O_direct_delta" else controls[pair_id]
                condition = {
                    "format": "direct_injection_channel_condition_7dh_v1",
                    "condition_name": f"{'O' if condition_type.startswith('O') else 'S'}{k}_{condition_type}",
                    "condition_type": condition_type,
                    "k": k,
                    "state_example_id": str(pair["state_example_id"]),
                    "state_task_id": str(pair["state_task_id"]),
                    "selector_transition_id": str(pair["transition_id"]),
                    "target_pair_id": pair_id,
                    "delta_source_pair_id": source_pair,
                    "audit_stratum": str(pair["audit_stratum"]),
                    "procedural_tier": int(pair["procedural_tier"]),
                    "signature_class_id": str(pair["signature_class_id"]),
                    "student_prompt_contains_raw_transition": False,
                    "selection_uses_behavioral_outcomes": False,
                    "valid_for_generation": True,
                }
                condition["condition_key"] = canonical_sha256(condition)
                conditions.append(condition)
    payload = {
        "format": "direct_injection_channel_condition_manifest_7dh_v1",
        "global_seed": GLOBAL_SEED,
        "state_count": int(manifest["pair_count"]),
        "condition_count": len(conditions),
        "condition_name_counts": {
            name: sum(str(row["condition_name"]) == name for row in conditions)
            for name in sorted({str(row["condition_name"]) for row in conditions})
        },
        "conditions": conditions,
        "missing_by_k": {
            str(k): manifest["feasibility"][str(k)]["missing_pair_ids"] for k in K_VALUES
        },
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    paths["condition_manifest"].parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(paths["condition_manifest"], payload)
    rate = float(run["runtime"]["rates"]["expected"]["generation"])
    report = {
        "format": "direct_injection_channel_one_step_preflight_7dh_v1",
        "condition_count": len(conditions),
        "qwen_generation_count": len(conditions),
        "appworld_reconstruction_execution_count": len(conditions),
        "expected_h100_hours": len(conditions) * rate / 3600.0,
        "manifest_sha256": payload["manifest_sha256"],
        "student_prompt_contains_raw_transition": False,
        "passed": len(conditions)
        == 2
        * sum(int(manifest["feasibility"][str(k)]["feasible_count"]) for k in K_VALUES),
    }
    atomic_write_json(paths["one_step_preflight"], report)
    return report


def _load_final_deltas(
    training: Mapping[str, Any],
) -> dict[int, tuple[list[str], torch.Tensor, str]]:
    output = {}
    for k in K_VALUES:
        result = training["k_results"][str(k)]
        checkpoint = Path(str(result["final_checkpoint"]))
        if sha256_file(checkpoint) != str(result["final_checkpoint_sha256"]):
            raise ValueError(f"K{k} final checkpoint hash changed")
        payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
        output[k] = (
            [str(value) for value in payload["pair_ids"]],
            payload["deltas"].to(torch.float32),
            str(result["final_checkpoint_sha256"]),
        )
    return output


def _live_delta_ratio(
    *,
    backend: HFQwenBackend,
    injector: DirectDeltaInjector,
    messages: Sequence[Mapping[str, str]],
    delta: torch.Tensor,
) -> dict[str, Any]:
    tokenized = backend.tokenize_messages(list(messages), add_generation_prompt=True)
    input_ids = tokenized.input_ids.to(backend.device)
    attention = tokenized.attention_mask.to(backend.device)
    indices = torch.tensor(
        [tokenized.metadata.get("last_user_token_indices") or []],
        device=backend.device,
        dtype=torch.long,
    )
    selected, metadata = injector._select_indices(
        input_ids, attention, None, indices
    )
    valid = selected[0][selected[0] >= 0]
    if int(valid.numel()) != injector.num_tokens:
        raise ValueError(
            f"Live prompt exposes {valid.numel()} tokens, expected {injector.num_tokens}"
        )
    with torch.no_grad():
        embeddings = backend.model.get_input_embeddings()(input_ids)
        base_norm = embeddings[0, valid].to(torch.float32).flatten().norm()
        delta_norm = delta.to(torch.float32).flatten().norm()
        ratio = delta_norm / base_norm.clamp_min(1.0e-12)
    return {
        "ratio": float(ratio.cpu()),
        "delta_norm": float(delta_norm.cpu()),
        "base_norm": float(base_norm.cpu()),
        "selected_token_indices": metadata["selected_token_indices"][0],
    }


def _run_condition(
    *,
    condition: Mapping[str, Any],
    delta: torch.Tensor,
    checkpoint_sha256: str,
    output_path: Path,
    stderr_path: Path,
    ordinal: int,
    attempt_id: str,
    replay: Mapping[str, Any],
    manifest: Mapping[str, Any],
    example: Any,
    record: Any,
    backend: HFQwenBackend,
    semantic_path: Path,
    bridge_script: Path,
) -> tuple[dict[str, Any], bool]:
    model_name = str(replay["causal_audit"]["generation"]["model_name"])
    if output_path.exists():
        row = _json(output_path)
        checks = {
            "key": str(row.get("condition_key")) == str(condition["condition_key"]),
            "manifest": str(row.get("condition_manifest_sha256"))
            == str(manifest["manifest_sha256"]),
            "checkpoint": str(row.get("delta_checkpoint_sha256")) == checkpoint_sha256,
            "model": str(row.get("model_name")) == model_name,
            "complete": str(row.get("status")) == "complete",
        }
        if not all(checks.values()):
            raise ValueError(f"Existing direct-channel row differs: {checks}")
        return row, True
    started = time.perf_counter()
    contract = _state_contract(example, record)
    prepare = _prepare_message(
        condition=condition,
        contract=contract,
        settings={"legacy": replay["legacy"], "replay": replay["replay"]},
        semantic_path=semantic_path,
        bridge_attempt=f"{attempt_id}-{ordinal:04d}-{time.time_ns()}",
    )
    client = LiveBridgeClient(
        executable=Path(str(replay["legacy"]["executable"])),
        bridge_script=bridge_script,
        appworld_root=Path(str(replay["legacy"]["appworld_root"])),
        stderr_path=stderr_path,
        timeout_seconds=float(replay["replay"]["subprocess_timeout_seconds"]),
    )
    injector = DirectDeltaInjector(
        model_dim=int(backend.model.config.hidden_size), num_tokens=int(condition["k"])
    ).to(backend.device)
    try:
        ready = client.prepare(prepare)
        generation_settings = replay["causal_audit"]["generation"]
        messages = build_live_appworld_messages(
            example,
            list(ready["actual_observations"]),
            prompt_profile=str(generation_settings["prompt_profile"]),
        )
        rendered = backend.render_messages(messages, add_generation_prompt=True)
        prompt_tokens = len(
            backend.tokenizer(rendered, add_special_tokens=True, truncation=False)[
                "input_ids"
            ]
        )
        remaining = int(generation_settings["context_limit"]) - prompt_tokens
        if remaining <= 0:
            raise RuntimeError(f"Direct-channel prompt is over context: {condition['condition_key']}")
        ratio = _live_delta_ratio(
            backend=backend, injector=injector, messages=messages, delta=delta
        )
        memory_z = delta.to(backend.device).reshape(1, -1)
        generation_started = time.perf_counter()
        output = backend.generate(
            messages=list(messages),
            max_new_tokens=min(int(generation_settings["max_new_tokens"]), remaining),
            temperature=0.0,
            top_p=1.0,
            injector=injector,
            memory_z=memory_z,
        )
        generation_seconds = time.perf_counter() - generation_started
        code, fixed_response = extract_code_and_fix_content(output.text)
        executed = client.execute(
            condition_key=str(condition["condition_key"]),
            ready_nonce=str(ready["ready_nonce"]),
            code=code,
            expected_target_observation=str(contract["target_observation"]),
        )
    except BaseException:
        client.terminate()
        raise
    metrics = evaluate_generated_action(
        output.text,
        code,
        str(contract["target_action"]),
        str(executed["raw_observation"]),
        str(contract["target_observation"]),
    )
    if executed["execution_exception"] is not None:
        metrics["execution_success"] = False
        metrics["exception_category"] = str(
            executed["execution_exception"].get("type", "exception")
        ).lower()
    metrics["semantic_successor_match"] = bool(executed["target_semantic_match"])
    row = {
        "format": RESULT_FORMAT,
        "status": "complete",
        **{key: value for key, value in condition.items() if key != "format"},
        "raw_model_response": output.text,
        "fixed_model_response": fixed_response,
        "extracted_code": code,
        "execution_output": str(executed["raw_observation"]),
        "normalized_observation": str(executed["locked_normalized_observation"]),
        "metrics": metrics,
        "target_action_sha256": contract["target_action_sha256"],
        "target_observation_sha256": contract["target_observation_sha256"],
        "live_worker": {
            "same_world_execution": bool(executed["same_world_execution"]),
            "same_python_namespace": bool(executed["same_python_namespace"]),
            "history_semantic_v3_match": bool(ready["history_semantic_v3_match"]),
            "execution_exception": executed["execution_exception"],
            "state_before": executed["state_before"],
            "state_after": executed["state_after"],
            "target_semantic_comparison": executed["target_semantic_comparison"],
        },
        "injection_position": "last_user_k",
        "injection_k": int(condition["k"]),
        "delta_ratio": ratio,
        "delta_checkpoint_sha256": checkpoint_sha256,
        "condition_manifest_sha256": str(manifest["manifest_sha256"]),
        "model_name": model_name,
        "prompt_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": int(output.usage["completion_tokens"]),
        "generation_elapsed_seconds": generation_seconds,
        "condition_elapsed_seconds": time.perf_counter() - started,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_path, row)
    return row, False


def _one_step(
    *,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    run: Mapping[str, Any],
    replay: Mapping[str, Any],
    artifact_dir: Path,
    attempt: AttemptLedger,
    attempt_id: str,
) -> dict[str, Any]:
    paths = _paths(direct, g3, run, artifact_dir)
    if not bool(_json(paths["one_step_preflight"])["passed"]):
        raise RuntimeError("EXP-026A one-step preflight failed")
    manifest = _json(paths["condition_manifest"])
    training = _json(paths["training_summary"])
    delta_tables = _load_final_deltas(training)
    backend = _build_backend_from_generation(replay["causal_audit"]["generation"])
    examples = _examples_by_state(load_decision_examples(paths["decisions"]))
    records = _records_by_task(load_memory_records(paths["memories"]))
    output_dir = artifact_dir / "one_step/condition_outputs"
    started = time.perf_counter()
    generation_seconds = 0.0
    completed = []
    resumed = 0
    for ordinal, condition in enumerate(manifest["conditions"], start=1):
        k = int(condition["k"])
        pair_ids, deltas, checkpoint_sha = delta_tables[k]
        positions = {pair_id: index for index, pair_id in enumerate(pair_ids)}
        source = str(condition["delta_source_pair_id"])
        delta = deltas[positions[source]]
        key = str(condition["condition_key"])
        row, reused = _run_condition(
            condition=condition,
            delta=delta,
            checkpoint_sha256=checkpoint_sha,
            output_path=output_dir / condition_checkpoint_name(key),
            stderr_path=artifact_dir / f"one_step/worker_logs/{key}.stderr.log",
            ordinal=ordinal,
            attempt_id=attempt_id,
            replay=replay,
            manifest=manifest,
            example=examples[str(condition["state_example_id"])],
            record=records[str(condition["state_task_id"])],
            backend=backend,
            semantic_path=paths["semantic_module"],
            bridge_script=paths["bridge_script"],
        )
        resumed += int(reused)
        generation_seconds += 0.0 if reused else float(row["generation_elapsed_seconds"])
        completed.append(row)
        attempt.progress(
            status="direct_channel_one_step",
            completed_conditions=len(completed),
            total_conditions=len(manifest["conditions"]),
            latest_validated_checkpoint=str(
                output_dir / condition_checkpoint_name(key)
            ),
        )
        print(
            f"direct channel {len(completed)}/{len(manifest['conditions'])} "
            f"{condition['condition_name']}",
            flush=True,
        )
    summary = {
        "format": "direct_injection_channel_generation_summary_7dh_v1",
        "condition_count": len(completed),
        "unique_condition_count": len({str(row["condition_key"]) for row in completed}),
        "resumed_condition_count": resumed,
        "new_condition_count": len(completed) - resumed,
        "qwen_generation_seconds": generation_seconds,
        "qwen_generation_h100_hours": generation_seconds / 3600.0,
        "elapsed_seconds": time.perf_counter() - started,
        "same_world_count": sum(row["live_worker"]["same_world_execution"] for row in completed),
        "same_namespace_count": sum(row["live_worker"]["same_python_namespace"] for row in completed),
        "execution_exception_count": sum(
            row["live_worker"]["execution_exception"] is not None for row in completed
        ),
        "passed": len(completed) == len(manifest["conditions"])
        and len({str(row["condition_key"]) for row in completed})
        == len(manifest["conditions"])
        and all(row["live_worker"]["same_world_execution"] for row in completed)
        and all(row["live_worker"]["same_python_namespace"] for row in completed),
    }
    atomic_write_json(paths["generation"], summary)
    return summary


def _positive_tasks(
    rows: Sequence[Mapping[str, Any]], *, condition_name: str
) -> tuple[int, dict[str, Any]]:
    grouped: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        grouped[str(row["state_task_id"])][str(row["condition_name"])].append(row)
    details = {}
    for task_id, values in sorted(grouped.items()):
        oracle = {str(row["state_example_id"]): row for row in values.get(condition_name, [])}
        bare = {str(row["state_example_id"]): row for row in values.get("C0_bare", [])}
        shared = sorted(set(oracle) & set(bare))
        if not shared:
            continue
        signature = statistics.fmean(
            float(oracle[key]["metrics"]["canonical_procedural_signature_match"])
            - float(bare[key]["metrics"]["canonical_procedural_signature_match"])
            for key in shared
        )
        successor = statistics.fmean(
            float(oracle[key]["metrics"]["semantic_successor_match"])
            - float(bare[key]["metrics"]["semantic_successor_match"])
            for key in shared
        )
        details[task_id] = {
            "paired_state_count": len(shared),
            "action_signature_difference": signature,
            "semantic_successor_difference": successor,
            "positive_relative_behavior": signature > 0.0 or successor > 0.0,
        }
    return sum(value["positive_relative_behavior"] for value in details.values()), details


def _analyze(
    *,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    run: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    paths = _paths(direct, g3, run, artifact_dir)
    generation = _json(paths["generation"])
    if not bool(generation["passed"]):
        raise RuntimeError("Direct-channel one-step infrastructure is invalid")
    manifest = _json(paths["condition_manifest"])
    outputs = [
        _json(path)
        for path in sorted((artifact_dir / "one_step/condition_outputs").glob("*.json"))
    ]
    c0_all = _load_parent_rows(paths["parent_c0_outputs"], "C0_bare")
    f3_all = _load_parent_rows(paths["parent_f3_outputs"], "F3_deployment_e_field_raw")
    pair_states = {str(row["state_example_id"]) for row in _json(paths["manifest"])["pairs"]}
    c0 = [row for row in c0_all if str(row["state_example_id"]) in pair_states]
    f3 = [row for row in f3_all if str(row["state_example_id"]) in pair_states]
    if len(c0) != 32 or len(f3) != 32:
        raise ValueError(f"Expected 32 C0/F3 rows, got {len(c0)}/{len(f3)}")
    by_k = {}
    passing = []
    for k in K_VALUES:
        o_name = f"O{k}_O_direct_delta"
        s_name = f"S{k}_S_shuffled_delta"
        feasible_states = {
            str(row["state_example_id"])
            for row in manifest["conditions"]
            if int(row["k"]) == k
        }
        rows = [
            row
            for row in outputs + c0 + f3
            if str(row["state_example_id"]) in feasible_states
            and str(row["condition_name"]) in {o_name, s_name, "C0_bare", "F3_deployment_e_field_raw"}
        ]
        comparisons = {
            "O_minus_C0": comparison_set(
                rows,
                left=o_name,
                right="C0_bare",
                bootstrap_samples=int(run["bootstrap_samples"]),
                seed=GLOBAL_SEED,
                per_metric_seed_offset=False,
            ),
            "O_minus_F3": comparison_set(
                rows,
                left=o_name,
                right="F3_deployment_e_field_raw",
                bootstrap_samples=int(run["bootstrap_samples"]),
                seed=GLOBAL_SEED,
                per_metric_seed_offset=False,
            ),
            "O_minus_S": comparison_set(
                rows,
                left=o_name,
                right=s_name,
                bootstrap_samples=int(run["bootstrap_samples"]),
                seed=GLOBAL_SEED,
                per_metric_seed_offset=False,
            ),
            "F3_minus_C0": comparison_set(
                rows,
                left="F3_deployment_e_field_raw",
                right="C0_bare",
                bootstrap_samples=int(run["bootstrap_samples"]),
                seed=GLOBAL_SEED,
                per_metric_seed_offset=False,
            ),
        }
        positive, details = _positive_tasks(rows, condition_name=o_name)
        gate = channel_gate(
            o_minus_c0=comparisons["O_minus_C0"],
            o_minus_s=comparisons["O_minus_S"],
            f3_minus_c0=comparisons["F3_minus_C0"],
            positive_task_count=positive,
            material_improvement=float(run["gate"]["material_shuffle_improvement"]),
            material_degradation=float(run["gate"]["material_degradation_tolerance"]),
        )
        by_k[str(k)] = {
            "k": k,
            "feasible_state_count": len(feasible_states),
            "condition_metrics": condition_summary(rows),
            "per_task": per_task_summary(rows),
            "comparisons": comparisons,
            "positive_task_count": positive,
            "positive_task_details": details,
            "gate": gate,
        }
        if bool(gate["passed"]):
            passing.append(k)
    if not passing:
        branch = "input_embedding_channel_behavioral_capacity_failed"
    elif 4 in passing:
        branch = "k4_channel_valid_amortization_bottleneck"
    elif 8 in passing:
        branch = "k8_channel_required"
    else:
        branch = "k16_channel_required"
    summary = {
        "format": "direct_injection_channel_analysis_7dh_v1",
        "run_uuid": str(run["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "k_results": by_k,
        "passing_k_values": passing,
        "smallest_passing_k": min(passing) if passing else None,
        "decision_branch": branch,
        "channel_capacity_passed": bool(passing),
        "conditional_widened_program_triggered": bool(passing),
        "r16_r64_pairmlp_program_training_performed_before_channel_gate": False,
        "generation": generation,
    }
    atomic_write_json(paths["analysis"], summary)
    lines = [
        "# EXP-026A Direct Injection-Channel One-Step Capacity Audit",
        "",
        f"- run UUID: `{run['run_uuid']}`",
        f"- global seed: `{GLOBAL_SEED}`",
        f"- conditions: `{generation['condition_count']}`",
        f"- decision branch: `{branch}`",
        f"- passing K values: `{passing}`",
        "",
    ]
    for k in K_VALUES:
        value = by_k[str(k)]
        metrics = value["condition_metrics"]
        o = metrics[f"O{k}_O_direct_delta"]["metrics"]
        s = metrics[f"S{k}_S_shuffled_delta"]["metrics"]
        retention = value["gate"]["retention"]
        lines.extend(
            [
                f"## K={k}",
                "",
                f"- feasible states: `{value['feasible_state_count']}`",
                f"- O exact API/signature/execution/successor: "
                f"`{o['exact_primary_app_api_match']:.6f}` / "
                f"`{o['canonical_procedural_signature_match']:.6f}` / "
                f"`{o['execution_success']:.6f}` / "
                f"`{o['semantic_successor_match']:.6f}`",
                f"- S signature/successor: "
                f"`{s['canonical_procedural_signature_match']:.6f}` / "
                f"`{s['semantic_successor_match']:.6f}`",
                f"- retention signature/successor: `{retention}`",
                f"- positive tasks: `{value['positive_task_count']}/9`",
                f"- gate passed: `{value['gate']['passed']}`",
                "",
            ]
        )
    atomic_write_text(artifact_dir / "direct_channel_report.md", "\n".join(lines))
    return summary


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    replay_cfg = load_config(args.replay_config)
    raw = cfg.raw
    direct = raw["stage_c_7dg"]
    g3 = raw["stage_c_7dg3"]
    policy = raw["stage_c_7dg3_policy"]
    run = raw["stage_c_7dh"]
    replay = replay_cfg.raw
    require_global_seed(int(run["global_seed"]))
    if os.name != "nt" and not os.path.ismount(Path(str(run["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    paths = _paths(direct, g3, run, args.artifact_dir)
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    data_hashes = {
        name: sha256_file(path)
        for name, path in {
            "config": args.config,
            "replay_config": args.replay_config,
            "parent_g3_analysis": paths["parent_g3_analysis"],
            "parent_policy_analysis": paths["parent_policy_analysis"],
            "parent_g3_manifest": paths["parent_g3_manifest"],
            "pairs_E": paths["pairs_E"],
            "selector": paths["selector"],
            "replay_lineage": paths["replay_lineage"],
            "decisions": paths["decisions"],
            "memories": paths["memories"],
            "transitions": paths["transitions"],
        }.items()
        if path.exists()
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(run["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"direct_injection_channel_{args.phase}",
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
                cfg=cfg,
                direct=direct,
                g3=g3,
                policy=policy,
                run=run,
                artifact_dir=args.artifact_dir,
            )
        elif args.phase == "teacher":
            result = _teacher_cache(
                cfg=cfg,
                direct=direct,
                g3=g3,
                policy=policy,
                run=run,
                replay=replay,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
            )
        elif args.phase == "train":
            result = _train(
                cfg=cfg,
                direct=direct,
                g3=g3,
                run=run,
                replay=replay,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
            )
        elif args.phase == "teacher_forced":
            result = _teacher_forced(
                direct=direct,
                g3=g3,
                run=run,
                artifact_dir=args.artifact_dir,
            )
        elif args.phase == "one_step_preflight":
            result = _one_step_preflight(
                direct=direct,
                g3=g3,
                run=run,
                artifact_dir=args.artifact_dir,
            )
        elif args.phase == "one_step":
            result = _one_step(
                direct=direct,
                g3=g3,
                run=run,
                replay=replay,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
                attempt_id=args.attempt_id,
            )
        else:
            result = _analyze(
                direct=direct,
                g3=g3,
                run=run,
                artifact_dir=args.artifact_dir,
            )
        checkpoint_by_phase = {
            "preflight": paths["preflight"],
            "teacher": paths["teacher_summary"],
            "train": paths["training_summary"],
            "teacher_forced": paths["teacher_forced"],
            "one_step_preflight": paths["one_step_preflight"],
            "one_step": paths["generation"],
            "analyze": paths["analysis"],
        }
        attempt.progress(
            status=f"direct_injection_channel_{args.phase}_completed",
            latest_validated_checkpoint=str(checkpoint_by_phase[args.phase]),
            result_passed=bool(
                result.get("passed", result.get("channel_capacity_passed", True))
            ),
        )
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
