from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
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
from torch import Tensor
import torch.nn.functional as F

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.config import load_config
from rcmf.model.backends.hf_qwen import HFQwenBackend
from rcmf.training.datasets import (
    load_decision_examples,
    load_memory_records,
)
from rcmf.training.oracle_convergence_5fa import atomic_torch_save, update_count_summary
from rcmf.training.oracle_decoder_5fc import LinearDeltaDecoder, module_state_sha256
from rcmf.training.procedural_causal_analysis_7b import (
    comparison_set,
    condition_summary,
    per_task_summary,
)
from rcmf.training.procedural_causal_audit_7b import condition_checkpoint_name
from rcmf.training.procedural_supervision_6f import canonical_procedure_signature
from rcmf.training.state_conditioned_program_7d import canonical_sha256, stable_key
from rcmf.training.state_conditioned_program_direct_7dg import (
    require_global_seed,
    seed_everything,
)
from rcmf.training.state_conditioned_program_pair_behavior_7dg3 import (
    pair_behavior_gate,
)
from rcmf.training.state_conditioned_program_policy_distill_7dg3 import (
    GLOBAL_SEED,
    POLICY_CONDITIONS,
    build_policy_behavior_manifest,
    build_policy_pair_manifest,
    policy_evaluation_diagnostics,
    sparse_policy_kl,
    summarize_policy_rows,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.training.transition_memory_6a import messages_with_transition_memory
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_jsonl,
)
from scripts.prepare_state_conditioned_program_7d import _context_builder
from scripts.run_procedural_causal_audit_7b import _examples_by_state, _records_by_task
from scripts.run_stage_c_oracle_capacity_5e import _collate, _precompute_direct_base_norms
from scripts.run_state_conditioned_program_direct_7dg import (
    PAIRMLP_NAME,
    _applied_delta,
    _load_manifests,
    _load_representations,
    _pair_indices,
    _predict_latents,
    _restore_rng,
    _student_forward,
)
from scripts.run_state_conditioned_program_pair_behavior_7dg3 import (
    _load_pairmlp,
    _load_parent_rows,
    _run_condition,
)
from scripts.run_transition_behavior_6a import _build_tokenized_rows


ROOT_NAME = "policy_distillation"
POLICY_CACHE_VERSION = "raw_memory_behavioral_policy_teacher_7dg3_v1"
POLICY_CHECKPOINT_VERSION = "behavioral_policy_pairmlp_checkpoint_7dg3_v1"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_state_conditioned_program_policy_distill_7dg3.yaml"
        ),
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
            "evaluate",
            "one_step_preflight",
            "one_step",
            "analyze",
        ),
        required=True,
    )
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--tmux-session", default="exp025dg3-policy")
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
    artifact_dir: Path,
) -> dict[str, Path]:
    root = artifact_dir / ROOT_NAME
    parent_direct = Path(str(g3["parent_direct"]))
    parent_g3 = artifact_dir
    parent_b = Path(str(direct["parent_exp025b"]))
    parent_c = Path(str(direct["parent_exp025c"]))
    parent_cr = Path(str(direct["parent_exp025cr"]))
    corpus = Path(str(direct["reconciled_corpus_dir"]))
    return {
        "root": root,
        "parent_g3_analysis": parent_g3 / "one_step/analysis.json",
        "parent_g3_manifest": parent_g3 / "one_step/condition_manifest.json",
        "parent_pairmlp_checkpoint": parent_direct / "pairmlp/checkpoints/model_u08.pt",
        "parent_pairmlp_training": parent_direct / "pairmlp/training_summary.json",
        "direct_split": parent_direct / "preflight/a_task_split.json",
        "direct_teacher_rows": parent_direct / "teacher_cache/rows",
        **{f"pairs_{cell}": parent_direct / f"preflight/pairs_{cell}.jsonl" for cell in "ABCDE"},
        "state_cache": parent_c / "representation_cache/multiview/state_multiview.pt",
        "transition_cache": parent_c / "representation_cache/multiview/transition_multiview.pt",
        "selector": parent_c / "selector/ensemble_scores.pt",
        "selector_conditions": parent_cr / "selector_condition_manifest.json",
        "parent_c0_outputs": parent_b / "condition_outputs",
        "parent_f3_outputs": parent_cr / "selector_condition_outputs",
        "replay_lineage": parent_b / "replay_validated_corpus_manifest.json",
        "decisions": corpus / "decision_examples.jsonl",
        "memories": corpus / "memory_records.jsonl",
        "transitions": parent_b
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        "semantic_module": Path("rcmf/training/appworld_replay_clean_rebuild_7b.py"),
        "bridge_script": Path("scripts/appworld_live_one_step_bridge_7b.py"),
        "pair_manifest": root / "pair_manifest.json",
        "preflight": root / "preflight.json",
        "teacher_summary": root / "teacher_cache/summary.json",
        "teacher_rows": root / "teacher_cache/rows",
        "checkpoint": root / "training/checkpoints/model_u08.pt",
        "latest_checkpoint": root / "training/latest_checkpoint.json",
        "training_summary": root / "training/training_summary.json",
        "evaluation_summary": root / "evaluation/summary.json",
        "behavior_manifest": root / "one_step/condition_manifest.json",
        "behavior_latents": root / "one_step/policy_latents.pt",
        "behavior_preflight": root / "one_step/preflight.json",
        "behavior_generation": root / "one_step/generation_summary.json",
        "behavior_analysis": root / "one_step/analysis.json",
    }


def _require_paths(paths: Mapping[str, Path], names: Sequence[str]) -> None:
    missing = {name: str(paths[name]) for name in names if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing policy-distillation inputs: {missing}")


def _runtime_projection(
    *,
    policy: Mapping[str, Any],
    unique_teacher_pairs: int,
    training_pairs: int,
    evaluation_pairs: int,
) -> dict[str, Any]:
    values = policy["runtime"]
    updates = int(training_pairs) * int(policy["training"]["updates_per_pair"])
    evaluation_controls = len(policy["evaluation"]["controls"])
    evaluation_forwards = int(evaluation_pairs) * evaluation_controls * 2
    one_step = int(values["one_step_generation_count"])
    scenarios = {}
    for name in ("best", "expected", "conservative"):
        generation = float(values["teacher_generation_seconds"][name])
        forward = float(values["forward_seconds"][name])
        backward = float(values["backward_seconds"][name])
        seconds = (
            unique_teacher_pairs * generation
            + unique_teacher_pairs * forward
            + updates * (2.0 * backward + forward)
            + evaluation_forwards * forward
            + one_step * generation
        )
        scenarios[name] = {
            "h100_hours": seconds / 3600.0,
            "teacher_generation_hours": unique_teacher_pairs * generation / 3600.0,
            "teacher_distribution_forward_hours": unique_teacher_pairs * forward / 3600.0,
            "training_hours": updates * (2.0 * backward + forward) / 3600.0,
            "evaluation_hours": evaluation_forwards * forward / 3600.0,
            "one_step_hours": one_step * generation / 3600.0,
        }
    artifact_bytes = (
        unique_teacher_pairs * int(values["projected_bytes_per_teacher_row"])
        + int(values["projected_bytes_per_checkpoint"])
        + one_step * int(values["projected_bytes_per_one_step_condition"])
    )
    return {
        "unique_teacher_pair_count": unique_teacher_pairs,
        "teacher_generation_count": unique_teacher_pairs,
        "teacher_distribution_forward_count": unique_teacher_pairs,
        "training_update_count": updates,
        "training_backward_equivalent_count": updates * 2,
        "training_swap_no_grad_forward_count": updates,
        "evaluation_forward_count": evaluation_forwards,
        "one_step_generation_count": one_step,
        "scenarios": scenarios,
        "projected_artifact_bytes": artifact_bytes,
    }


def _preflight(
    *,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    policy: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    paths = _paths(direct, g3, artifact_dir)
    required = (
        "parent_g3_analysis",
        "parent_g3_manifest",
        "parent_pairmlp_checkpoint",
        "parent_pairmlp_training",
        "direct_split",
        "state_cache",
        "transition_cache",
        "selector",
        "selector_conditions",
        "replay_lineage",
        "decisions",
        "memories",
        "transitions",
        "semantic_module",
        "bridge_script",
        *(f"pairs_{cell}" for cell in "ABCDE"),
    )
    _require_paths(paths, required)
    parent_analysis = _json(paths["parent_g3_analysis"])
    immutable_checks = {
        "global_seed": int(policy["global_seed"]) == GLOBAL_SEED,
        "parent_analysis_hash": sha256_file(paths["parent_g3_analysis"])
        == str(policy["parent_g3_analysis_sha256"]),
        "parent_branch": str(parent_analysis["decision_branch"])
        == str(policy["expected_parent_branch"]),
        "parent_gate_failed": not bool(parent_analysis["pairmlp_one_step_behavior_passed"]),
        "parent_checkpoint": sha256_file(paths["parent_pairmlp_checkpoint"])
        == str(policy["expected_pairmlp_checkpoint_sha256"]),
        "selector": sha256_file(paths["selector"]) == str(g3["expected_selector_ensemble_sha256"]),
        "replay_lineage": str(_json(paths["replay_lineage"])["lineage_sha256"])
        == str(g3["expected_replay_lineage_sha256"]),
    }
    if not all(immutable_checks.values()):
        raise ValueError(f"Policy parent integrity failed: {immutable_checks}")

    manifests = _load_manifests(paths)
    split = _json(paths["direct_split"])
    pair_manifest = build_policy_pair_manifest(
        manifests,
        split,
        training_count=int(policy["pair_manifest"]["training_count"]),
        evaluation_counts=policy["pair_manifest"]["evaluation_counts"],
        context_limit=int(policy["teacher"]["context_limit"]),
        max_new_tokens=int(policy["teacher"]["max_new_tokens"]),
        seed=GLOBAL_SEED,
    )
    direct_teacher_missing = [
        str(row["pair_id"])
        for row in pair_manifest["unique_pairs"]
        if not _row_path(paths["direct_teacher_rows"], str(row["pair_id"])).exists()
    ]
    if direct_teacher_missing:
        raise FileNotFoundError(
            f"Policy pairs lack clean direct teacher rows: {direct_teacher_missing[:5]}"
        )
    paths["root"].mkdir(parents=True, exist_ok=True)
    atomic_write_json(paths["pair_manifest"], pair_manifest)
    evaluation_pairs = sum(pair_manifest["evaluation_counts"].values())
    runtime = _runtime_projection(
        policy=policy,
        unique_teacher_pairs=int(pair_manifest["unique_teacher_pair_count"]),
        training_pairs=int(pair_manifest["training_count"]),
        evaluation_pairs=evaluation_pairs,
    )
    threshold = float(policy["runtime"]["review_threshold_h100_hours"])
    launch = float(runtime["scenarios"]["expected"]["h100_hours"]) <= threshold
    report = {
        "format": "behavioral_policy_distillation_preflight_7dg3_v1",
        "run_uuid": str(policy["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "immutable_checks": immutable_checks,
        "initialization": str(policy["initialization"]),
        "optimizer_resume": bool(policy["optimizer_resume"]),
        "training_pairs": int(pair_manifest["training_count"]),
        "training_tasks": int(pair_manifest["training_task_count"]),
        "evaluation_pairs": evaluation_pairs,
        "evaluation_counts": pair_manifest["evaluation_counts"],
        "unique_teacher_pairs": int(pair_manifest["unique_teacher_pair_count"]),
        "pair_manifest_sha256": sha256_file(paths["pair_manifest"]),
        "direct_teacher_rows_present": len(pair_manifest["unique_pairs"]),
        "runtime": runtime,
        "review_threshold_h100_hours": threshold,
        "automatic_launch_allowed": launch,
        "student_prompt_contains_raw_transition": False,
        "passed": launch,
    }
    atomic_write_json(paths["preflight"], report)
    atomic_write_text(
        paths["root"] / "runtime_preflight.md",
        "\n".join(
            [
                "# EXP-025D-G3 behavioral-policy distillation preflight",
                "",
                f"- global seed: `{GLOBAL_SEED}`",
                f"- training pairs: `{report['training_pairs']}` across `{report['training_tasks']}` tasks",
                f"- evaluation pairs: `{report['evaluation_pairs']}`",
                f"- unique raw-memory teacher generations: `{report['unique_teacher_pairs']}`",
                f"- training updates: `{runtime['training_update_count']}`",
                f"- expected H100 hours: `{runtime['scenarios']['expected']['h100_hours']:.4f}`",
                f"- conservative H100 hours: `{runtime['scenarios']['conservative']['h100_hours']:.4f}`",
                f"- 10-hour automatic-launch gate: `{str(launch).lower()}`",
                "- initialization: existing Direct PairMLP u8 and private decoder, fresh optimizer",
                "- raw transition in student prompt: `false`",
                "",
            ]
        ),
    )
    return report


def _build_backend_from_generation(generation: Mapping[str, Any]) -> HFQwenBackend:
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
        raise RuntimeError("Qwen must remain frozen")
    return backend


def _teacher_policy_row(
    *,
    backend: HFQwenBackend,
    context: Mapping[str, Any],
    pair: Mapping[str, Any],
    transition: Mapping[str, Any],
    prompt_profile: str,
    teacher_settings: Mapping[str, Any],
    structural_lineage: str,
) -> dict[str, Any]:
    messages = messages_with_transition_memory(context["base_messages"], transition, prompt_profile)
    rendered = backend.render_messages(messages, add_generation_prompt=True)
    if sha256_text(rendered) != str(pair["teacher_prompt_sha256"]):
        raise ValueError(f"Raw-memory teacher prompt hash differs for {pair['pair_id']}")
    prompt = backend.tokenize_messages(messages, add_generation_prompt=True)
    prompt_tokens = int(prompt.attention_mask.sum().item())
    max_new = int(teacher_settings["max_new_tokens"])
    if prompt_tokens + max_new > int(teacher_settings["context_limit"]):
        raise RuntimeError(
            f"Policy-teacher pair lacks locked generation headroom: {pair['pair_id']}"
        )
    started = time.perf_counter()
    generated = backend.generate(
        messages=messages,
        max_new_tokens=max_new,
        temperature=float(teacher_settings["temperature"]),
        top_p=float(teacher_settings["top_p"]),
    )
    generation_seconds = time.perf_counter() - started
    generated_ids = [int(value) for value in generated.token_ids]
    if not generated_ids:
        raise RuntimeError(f"Raw-memory teacher generated no tokens for {pair['pair_id']}")
    full_ids = torch.cat(
        (
            prompt.input_ids,
            torch.tensor([generated_ids], dtype=torch.long, device=backend.device),
        ),
        dim=1,
    )
    labels = torch.full_like(full_ids, -100)
    labels[:, prompt.input_ids.shape[1] :] = torch.tensor(
        [generated_ids], dtype=torch.long, device=backend.device
    )
    attention = torch.ones_like(full_ids)
    with torch.no_grad():
        scored = backend.forward_train(
            input_ids=full_ids,
            attention_mask=attention,
            labels=labels,
        )
        logits = scored.logits.to(torch.float32)
    if logits.shape[0] != len(generated_ids):
        raise ValueError("Teacher generated-token logits are misaligned")
    top_k = min(int(teacher_settings["top_k"]), logits.shape[-1])
    top_logits, top_ids = torch.topk(logits, k=top_k, dim=-1)
    log_normalizer = torch.logsumexp(logits.to(torch.float64), dim=-1)
    top_logprobs = top_logits.to(torch.float64) - log_normalizer.unsqueeze(1)
    top_probs = top_logprobs.exp()
    target_ids = torch.tensor(generated_ids, dtype=torch.long, device=logits.device)
    target_logprobs = F.log_softmax(logits.to(torch.float64), dim=-1)[
        torch.arange(len(generated_ids), device=logits.device), target_ids
    ]
    positions = []
    for index in range(len(generated_ids)):
        other = max(0.0, 1.0 - float(top_probs[index].sum().cpu()))
        positions.append(
            {
                "position": index,
                "teacher_token_id": generated_ids[index],
                "teacher_token_logprob": float(target_logprobs[index].cpu()),
                "top_token_ids": [int(value) for value in top_ids[index].cpu().tolist()],
                "top_logits": [float(value) for value in top_logits[index].cpu().tolist()],
                "top_logprobs": [float(value) for value in top_logprobs[index].cpu().tolist()],
                "top_probabilities": [float(value) for value in top_probs[index].cpu().tolist()],
                "other_probability": other,
            }
        )
    code, fixed_response = extract_code_and_fix_content(generated.text)
    generated_signature = canonical_procedure_signature(code)
    target_signature = canonical_procedure_signature(context["example"].target_text)
    return {
        "format": POLICY_CACHE_VERSION,
        "pair_id": str(pair["pair_id"]),
        "cell": str(pair["cell"]),
        "state_example_id": str(pair["state_example_id"]),
        "state_task_id": str(pair["state_task_id"]),
        "transition_id": str(pair["transition_id"]),
        "transition_parent_id": str(pair["transition_parent_id"]),
        "teacher_prompt_sha256": str(pair["teacher_prompt_sha256"]),
        "teacher_prompt_tokens": prompt_tokens,
        "context_limit": int(teacher_settings["context_limit"]),
        "max_new_tokens": max_new,
        "generation_config": {
            "temperature": float(teacher_settings["temperature"]),
            "top_p": float(teacher_settings["top_p"]),
            "do_sample": bool(teacher_settings["do_sample"]),
            "enable_thinking": bool(teacher_settings["enable_thinking"]),
        },
        "model_name": str(backend.model_name),
        "generated_token_ids": generated_ids,
        "generated_token_count": len(generated_ids),
        "generated_token_sha256": sha256_text(",".join(map(str, generated_ids))),
        "raw_response": generated.text,
        "fixed_response": fixed_response,
        "extracted_code": code,
        "generated_signature_sha256": generated_signature["signature_sha256"],
        "target_signature_sha256": target_signature["signature_sha256"],
        "exact_action_signature": generated_signature["signature_sha256"]
        == target_signature["signature_sha256"],
        "top_k": top_k,
        "positions": positions,
        "teacher_token_mean_nll": float(-target_logprobs.mean().cpu()),
        "hit_max_new_tokens": len(generated_ids) == max_new,
        "generation_seconds": generation_seconds,
        "execution_outcome": None,
        "execution_outcome_status": "not_run_training_cache_generation_only",
        "structural_lineage_sha256": structural_lineage,
        "student_prompt_contains_raw_transition": False,
    }


def _validate_teacher_row(
    row: Mapping[str, Any], pair: Mapping[str, Any], policy: Mapping[str, Any]
) -> None:
    checks = {
        "format": str(row.get("format")) == POLICY_CACHE_VERSION,
        "pair": str(row.get("pair_id")) == str(pair["pair_id"]),
        "state": str(row.get("state_example_id")) == str(pair["state_example_id"]),
        "transition": str(row.get("transition_id")) == str(pair["transition_id"]),
        "prompt": str(row.get("teacher_prompt_sha256")) == str(pair["teacher_prompt_sha256"]),
        "model": str(row.get("model_name")) == "Qwen/Qwen3-8B",
        "top_k": int(row.get("top_k", -1)) == int(policy["teacher"]["top_k"]),
        "tokens": int(row.get("generated_token_count", -1))
        == len(row.get("positions", []))
        == len(row.get("generated_token_ids", [])),
        "student_raw_absent": not bool(row.get("student_prompt_contains_raw_transition")),
    }
    if not all(checks.values()):
        raise ValueError(f"Policy teacher cache identity differs: {checks}")


def _teacher_cache(
    *,
    cfg: Any,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    policy: Mapping[str, Any],
    replay: Mapping[str, Any],
    artifact_dir: Path,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    paths = _paths(direct, g3, artifact_dir)
    preflight = _json(paths["preflight"])
    if not bool(preflight["automatic_launch_allowed"]):
        raise RuntimeError("Policy-distillation runtime preflight did not authorize GPU work")
    manifest = _json(paths["pair_manifest"])
    backend = _build_backend_from_generation(replay["causal_audit"]["generation"])
    examples = load_decision_examples(paths["decisions"])
    contexts, _ = _context_builder(
        tokenizer=backend.tokenizer,
        examples=examples,
        prompt_profile=cfg.benchmark.prompt_profile,
    )
    transitions = {str(row["transition_id"]): row for row in _rows(paths["transitions"])}
    paths["teacher_rows"].mkdir(parents=True, exist_ok=True)
    completed = 0
    reused = 0
    started = time.perf_counter()
    for pair in manifest["unique_pairs"]:
        output = _row_path(paths["teacher_rows"], str(pair["pair_id"]))
        if output.exists():
            row = _json(output)
            _validate_teacher_row(row, pair, policy)
            reused += 1
        else:
            row = _teacher_policy_row(
                backend=backend,
                context=contexts[str(pair["state_example_id"])],
                pair=pair,
                transition=transitions[str(pair["transition_id"])],
                prompt_profile=cfg.benchmark.prompt_profile,
                teacher_settings=policy["teacher"],
                structural_lineage=str(g3["expected_structural_lineage_sha256"]),
            )
            atomic_write_json(output, row)
        completed += 1
        attempt.progress(
            status="policy_teacher_cache",
            completed_pairs=completed,
            total_pairs=len(manifest["unique_pairs"]),
            latest_validated_checkpoint=str(output),
        )
        print(f"policy teacher {completed}/{len(manifest['unique_pairs'])}", flush=True)
    rows = [
        _json(_row_path(paths["teacher_rows"], str(pair["pair_id"])))
        for pair in manifest["unique_pairs"]
    ]
    summary = {
        "format": "behavioral_policy_teacher_cache_summary_7dg3_v1",
        "pair_count": len(rows),
        "unique_pair_count": len({str(row["pair_id"]) for row in rows}),
        "reused_rows": reused,
        "new_rows": len(rows) - reused,
        "generated_tokens": sum(int(row["generated_token_count"]) for row in rows),
        "hit_max_new_tokens": sum(bool(row["hit_max_new_tokens"]) for row in rows),
        "exact_action_signature_rate": statistics.fmean(
            float(row["exact_action_signature"]) for row in rows
        ),
        "generation_seconds": sum(float(row["generation_seconds"]) for row in rows),
        "elapsed_seconds": time.perf_counter() - started,
        "row_set_sha256": canonical_sha256(
            {
                str(row["pair_id"]): sha256_file(
                    _row_path(paths["teacher_rows"], str(row["pair_id"]))
                )
                for row in rows
            }
        ),
        "qwen_frozen": True,
        "passed": len(rows) == int(manifest["unique_teacher_pair_count"]),
    }
    atomic_write_json(paths["teacher_summary"], summary)
    return summary


def _policy_tokenized_row(
    *,
    backend: HFQwenBackend,
    context: Mapping[str, Any],
    pair: Mapping[str, Any],
    teacher: Mapping[str, Any],
) -> dict[str, Any]:
    tokenized = backend.tokenize_messages(context["base_messages"], add_generation_prompt=True)
    prompt_ids = [int(value) for value in tokenized.input_ids[0].cpu().tolist()]
    generated = [int(value) for value in teacher["generated_token_ids"]]
    full_ids = prompt_ids + generated
    if len(full_ids) > int(teacher["context_limit"]):
        raise ValueError(f"Policy student row exceeds context: {pair['pair_id']}")
    rendered = str(tokenized.metadata["text"])
    if sha256_text(rendered) != str(pair["prompt_sha256"]):
        raise ValueError(f"Bare policy-student prompt differs for {pair['pair_id']}")
    return {
        "pair_id": str(pair["pair_id"]),
        "state_example_id": str(pair["state_example_id"]),
        "state_task_id": str(pair["state_task_id"]),
        "transition_id": str(pair["transition_id"]),
        "transition_parent_id": str(pair["transition_parent_id"]),
        "cell": str(pair["cell"]),
        "input_ids": full_ids,
        "labels": [-100] * len(prompt_ids) + generated,
        "pad_token_id": int(backend.tokenizer.pad_token_id),
        "last_user_token_indices": [
            int(value) for value in tokenized.metadata["last_user_token_indices"]
        ],
        "target_len": len(generated),
        "response_cache": dict(teacher),
        "student_prompt_sha256": str(pair["prompt_sha256"]),
        "student_prompt_contains_raw_transition": False,
    }


def _load_training_data(
    *,
    backend: HFQwenBackend,
    cfg: Any,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    artifact_dir: Path,
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    paths = _paths(direct, g3, artifact_dir)
    manifest = _json(paths["pair_manifest"])
    examples = load_decision_examples(paths["decisions"])
    contexts, _ = _context_builder(
        tokenizer=backend.tokenizer,
        examples=examples,
        prompt_profile=cfg.benchmark.prompt_profile,
    )
    teachers = {
        str(pair["pair_id"]): _json(_row_path(paths["teacher_rows"], str(pair["pair_id"])))
        for pair in manifest["unique_pairs"]
    }
    policy_rows = {
        str(pair["pair_id"]): _policy_tokenized_row(
            backend=backend,
            context=contexts[str(pair["state_example_id"])],
            pair=pair,
            teacher=teachers[str(pair["pair_id"])],
        )
        for pair in manifest["unique_pairs"]
    }
    direct_responses = [
        _json(_row_path(paths["direct_teacher_rows"], str(pair["pair_id"])))
        for pair in manifest["unique_pairs"]
    ]
    ground_truth_rows = _build_tokenized_rows(
        backend=backend,
        examples=examples,
        response_rows=direct_responses,
        prompt_profile=cfg.benchmark.prompt_profile,
        context_limit=int(direct["teacher_cache"]["context_limit"]),
    )
    ground_truth = {str(row["pair_id"]): row for row in ground_truth_rows}
    if set(policy_rows) != set(ground_truth):
        raise ValueError("Policy and ground-truth tokenized pair sets differ")
    return manifest, teachers, policy_rows, ground_truth


def _swap_indices(rows: Sequence[Mapping[str, Any]]) -> list[int]:
    pair_ids = [str(row["pair_id"]) for row in rows]
    output = []
    for index, row in enumerate(rows):
        own = str(row["transition_id"])
        candidates = [
            other for other, candidate in enumerate(rows) if str(candidate["transition_id"]) != own
        ]
        if not candidates:
            raise ValueError("Policy swap control requires a different transition")
        output.append(
            min(
                candidates,
                key=lambda other: stable_key(
                    GLOBAL_SEED,
                    "policy-training-memory-swap",
                    pair_ids[index],
                    pair_ids[other],
                ),
            )
        )
    return output


def _checkpoint_payload(
    *,
    model: torch.nn.Module,
    decoder: LinearDeltaDecoder,
    optimizer: torch.optim.Optimizer,
    pair_ids: Sequence[str],
    update_counts: Sequence[int],
    completed_rounds: int,
    history: Sequence[Mapping[str, Any]],
    parent_checkpoint_sha256: str,
    pair_manifest_sha256: str,
) -> dict[str, Any]:
    return {
        "format": POLICY_CHECKPOINT_VERSION,
        "global_seed": GLOBAL_SEED,
        "model_name": PAIRMLP_NAME,
        "initialization": "warm_start_existing_direct_pairmlp_u8_and_private_decoder",
        "parent_checkpoint_sha256": parent_checkpoint_sha256,
        "pair_manifest_sha256": pair_manifest_sha256,
        "pair_ids": list(pair_ids),
        "update_counts": [int(value) for value in update_counts],
        "update_accounting": update_count_summary(pair_ids, update_counts),
        "completed_rounds": int(completed_rounds),
        "model_state_dict": {
            key: value.detach().cpu() for key, value in model.state_dict().items()
        },
        "decoder_state_dict": {
            key: value.detach().cpu() for key, value in decoder.state_dict().items()
        },
        "optimizer_state_dict": optimizer.state_dict(),
        "history": list(history),
        "model_sha256": module_state_sha256(model),
        "decoder_sha256": module_state_sha256(decoder),
        "python_random_state": random.getstate(),
        "torch_rng_state": torch.get_rng_state(),
        "cuda_rng_state": (torch.cuda.get_rng_state_all() if torch.cuda.is_available() else []),
    }


def _policy_loss(
    student_logits: Tensor,
    teacher: Mapping[str, Any],
) -> tuple[Tensor, dict[str, Tensor]]:
    kl = sparse_policy_kl(student_logits, teacher["positions"])
    target_ids = torch.tensor(
        teacher["generated_token_ids"], dtype=torch.long, device=student_logits.device
    )
    ce = F.cross_entropy(student_logits.to(torch.float32), target_ids)
    top1 = (student_logits.argmax(dim=-1) == target_ids).to(torch.float32).mean()
    return kl, {"policy_kl": kl, "teacher_token_ce": ce, "top1": top1}


def _train(
    *,
    cfg: Any,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    policy: Mapping[str, Any],
    replay: Mapping[str, Any],
    artifact_dir: Path,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    paths = _paths(direct, g3, artifact_dir)
    _require_paths(paths, ("teacher_summary", "pair_manifest", "parent_pairmlp_checkpoint"))
    if not bool(_json(paths["teacher_summary"])["passed"]):
        raise RuntimeError("Policy teacher cache is incomplete")
    backend = _build_backend_from_generation(replay["causal_audit"]["generation"])
    manifest, teachers, policy_rows, ground_truth = _load_training_data(
        backend=backend,
        cfg=cfg,
        direct=direct,
        g3=g3,
        artifact_dir=artifact_dir,
    )
    representations = _load_representations(paths, backend.device)
    parent_training = _json(paths["parent_pairmlp_training"])
    model, decoder, _ = _load_pairmlp(
        checkpoint_path=paths["parent_pairmlp_checkpoint"],
        direct=direct,
        transition_view_names=representations["transition_view_names"],
    )
    model = model.to(backend.device)
    decoder = decoder.to(backend.device)
    model.train()
    decoder.train()
    for parameter in model.parameters():
        parameter.requires_grad_(True)
    for parameter in decoder.parameters():
        parameter.requires_grad_(True)
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Qwen became trainable during policy distillation")
    train_pairs = list(manifest["training_pairs"])
    train_rows = [policy_rows[str(pair["pair_id"])] for pair in train_pairs]
    gt_rows = [ground_truth[str(pair["pair_id"])] for pair in train_pairs]
    pair_ids = [str(row["pair_id"]) for row in train_rows]
    state_indices, transition_indices = _pair_indices(train_rows, representations)
    states = representations["state_values"][state_indices]
    transitions = representations["transition_values"][transition_indices]
    swap = _swap_indices(train_rows)
    base_norms = _precompute_direct_base_norms(
        backend=backend, rows=train_rows, device=backend.device, k=4
    ).to(backend.device)
    optimizer = torch.optim.AdamW(
        [
            {
                "params": list(model.parameters()),
                "lr": float(policy["training"]["program_learning_rate"]),
            },
            {
                "params": list(decoder.parameters()),
                "lr": float(policy["training"]["decoder_learning_rate"]),
            },
        ],
        weight_decay=float(policy["training"]["weight_decay"]),
    )
    update_counts = [0] * len(train_rows)
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
            "format": str(payload.get("format")) == POLICY_CHECKPOINT_VERSION,
            "seed": int(payload.get("global_seed", -1)) == GLOBAL_SEED,
            "pairs": list(payload.get("pair_ids", [])) == pair_ids,
            "parent": str(payload.get("parent_checkpoint_sha256"))
            == str(policy["expected_pairmlp_checkpoint_sha256"]),
            "manifest": str(payload.get("pair_manifest_sha256"))
            == str(manifest["manifest_sha256"]),
        }
        if not all(checks.values()):
            raise ValueError(f"Policy training resume identity differs: {checks}")
        model.load_state_dict(payload["model_state_dict"])
        decoder.load_state_dict(payload["decoder_state_dict"])
        optimizer.load_state_dict(payload["optimizer_state_dict"])
        update_counts = [int(value) for value in payload["update_counts"]]
        completed = int(payload["completed_rounds"])
        history = list(payload["history"])
        _restore_rng(payload)

    settings = policy["training"]
    total_rounds = int(settings["updates_per_pair"])
    started = time.perf_counter()
    for update_round in range(completed + 1, total_rounds + 1):
        order = sorted(
            range(len(train_rows)),
            key=lambda index: stable_key(
                GLOBAL_SEED,
                f"policy-distillation-round-{update_round}",
                pair_ids[index],
            ),
        )
        metrics: dict[str, list[float]] = {
            name: []
            for name in (
                "loss",
                "policy_kl",
                "teacher_token_ce",
                "teacher_token_top1_accuracy",
                "ground_truth_ce",
                "swap_ce",
                "swap_contrast",
                "maximum_ratio",
            )
        }
        for index in order:
            optimizer.zero_grad(set_to_none=True)
            state = states[index].unsqueeze(0).to(backend.device)
            transition = transitions[index].unsqueeze(0).to(backend.device)
            z = model(state, transition)
            delta, ratio, raw_delta = _applied_delta(
                decoder=decoder, z=z, base_norms=base_norms[index : index + 1]
            )
            batch = _collate([train_rows[index]], device=backend.device, k=4)
            student = _student_forward(
                backend=backend, batch=batch, delta=delta, prefix_enabled=False
            )
            kl, terms = _policy_loss(student["target_logits"], teachers[pair_ids[index]])
            with torch.no_grad():
                swap_z = model(
                    state,
                    transitions[swap[index]].unsqueeze(0).to(backend.device),
                )
                swap_delta, swap_ratio, _ = _applied_delta(
                    decoder=decoder,
                    z=swap_z,
                    base_norms=base_norms[index : index + 1],
                )
                swap_student = _student_forward(
                    backend=backend,
                    batch=batch,
                    delta=swap_delta,
                    prefix_enabled=False,
                )
                swap_ce = swap_student["loss"].to(torch.float32)
            contrast = F.relu(
                float(settings["memory_swap_margin"]) + terms["teacher_token_ce"] - swap_ce
            )
            raw_ratio = raw_delta.to(torch.float32).flatten(start_dim=1).norm(dim=1) / base_norms[
                index : index + 1
            ].clamp_min(1.0e-12)
            main_loss = (
                float(settings["policy_kl_weight"]) * kl
                + float(settings["teacher_token_ce_weight"]) * terms["teacher_token_ce"]
                + float(settings["memory_swap_weight"]) * contrast
                + float(settings["ratio_restraint_weight"])
                * (F.relu(raw_ratio - 1.0).pow(2).mean() + 0.01 * z.pow(2).mean())
            )
            main_loss.backward()
            gt_z = model(state, transition)
            gt_delta, gt_ratio, _ = _applied_delta(
                decoder=decoder,
                z=gt_z,
                base_norms=base_norms[index : index + 1],
            )
            gt_batch = _collate([gt_rows[index]], device=backend.device, k=4)
            gt_student = _student_forward(
                backend=backend,
                batch=gt_batch,
                delta=gt_delta,
                prefix_enabled=False,
            )
            gt_loss = float(settings["ground_truth_ce_weight"]) * gt_student["loss"].to(
                torch.float32
            )
            gt_loss.backward()
            torch.nn.utils.clip_grad_norm_(
                list(model.parameters()) + list(decoder.parameters()),
                float(settings["max_grad_norm"]),
            )
            optimizer.step()
            update_counts[index] += 1
            maximum_ratio = max(
                float(ratio["maximum_ratio"].detach().cpu()),
                float(swap_ratio["maximum_ratio"].detach().cpu()),
                float(gt_ratio["maximum_ratio"].detach().cpu()),
            )
            if maximum_ratio > float(settings["ratio_budget"]) + 1.0e-4:
                raise RuntimeError("Policy training perturbation ratio exceeds 1.0")
            metrics["loss"].append(float((main_loss + gt_loss).detach().cpu()))
            metrics["policy_kl"].append(float(kl.detach().cpu()))
            metrics["teacher_token_ce"].append(float(terms["teacher_token_ce"].detach().cpu()))
            metrics["teacher_token_top1_accuracy"].append(float(terms["top1"].detach().cpu()))
            metrics["ground_truth_ce"].append(float(gt_student["loss"].detach().cpu()))
            metrics["swap_ce"].append(float(swap_ce.detach().cpu()))
            metrics["swap_contrast"].append(float(contrast.detach().cpu()))
            metrics["maximum_ratio"].append(maximum_ratio)

        accounting = update_count_summary(pair_ids, update_counts)
        if (
            not accounting["all_pairs_equal"]
            or int(accounting["minimum_updates_per_pair"]) != update_round
        ):
            raise RuntimeError(f"Policy updates are unequal after u{update_round}")
        entry = {
            "updates_per_pair": update_round,
            "metrics": {name: statistics.fmean(values) for name, values in metrics.items()},
            "maximum_applied_ratio": max(metrics["maximum_ratio"]),
            "update_accounting": accounting,
            "elapsed_seconds": time.perf_counter() - started,
        }
        history.append(entry)
        payload = _checkpoint_payload(
            model=model,
            decoder=decoder,
            optimizer=optimizer,
            pair_ids=pair_ids,
            update_counts=update_counts,
            completed_rounds=update_round,
            history=history,
            parent_checkpoint_sha256=str(policy["expected_pairmlp_checkpoint_sha256"]),
            pair_manifest_sha256=str(manifest["manifest_sha256"]),
        )
        checkpoint = (
            paths["checkpoint"]
            if update_round == total_rounds
            else paths["root"] / "training/checkpoints/latest.pt"
        )
        atomic_torch_save(payload, checkpoint)
        atomic_write_json(
            paths["latest_checkpoint"],
            {"checkpoint": str(checkpoint), "updates_per_pair": update_round},
        )
        attempt.progress(
            status=f"policy_pairmlp_u{update_round}",
            updates_per_pair=update_round,
            latest_validated_checkpoint=str(checkpoint),
        )
        print(
            f"policy PairMLP u{update_round} "
            f"kl={entry['metrics']['policy_kl']:.6f} "
            f"ce={entry['metrics']['teacher_token_ce']:.6f}",
            flush=True,
        )
    if completed >= total_rounds and not paths["checkpoint"].exists():
        latest = torch.load(
            Path(str(_json(paths["latest_checkpoint"])["checkpoint"])),
            map_location="cpu",
            weights_only=False,
        )
        atomic_torch_save(latest, paths["checkpoint"])
    final = torch.load(paths["checkpoint"], map_location="cpu", weights_only=False)
    summary = {
        "format": "behavioral_policy_pairmlp_training_summary_7dg3_v1",
        "global_seed": GLOBAL_SEED,
        "training_pair_count": len(train_rows),
        "training_task_count": len({str(row["state_task_id"]) for row in train_rows}),
        "updates_per_pair": int(final["completed_rounds"]),
        "update_accounting": final["update_accounting"],
        "history": final["history"],
        "parent_pairmlp_checkpoint_sha256": str(policy["expected_pairmlp_checkpoint_sha256"]),
        "policy_checkpoint_sha256": sha256_file(paths["checkpoint"]),
        "model_sha256": str(final["model_sha256"]),
        "decoder_sha256": str(final["decoder_sha256"]),
        "qwen_frozen": True,
        "selector_unchanged": sha256_file(paths["selector"])
        == str(g3["expected_selector_ensemble_sha256"]),
        "student_prompt_contains_raw_transition": False,
        "maximum_applied_ratio": max(
            float(row["maximum_applied_ratio"]) for row in final["history"]
        ),
        "elapsed_seconds": float(final["history"][-1]["elapsed_seconds"]),
        "passed": int(final["completed_rounds"]) == int(policy["training"]["updates_per_pair"])
        and bool(final["update_accounting"]["all_pairs_equal"]),
        "warm_start_model_sha256": str(parent_training["model_sha256"]),
        "warm_start_decoder_sha256": str(parent_training["trained_decoder_sha256"]),
    }
    atomic_write_json(paths["training_summary"], summary)
    return summary


def _load_policy_model(
    *,
    checkpoint: Path,
    direct: Mapping[str, Any],
    transition_view_names: Sequence[str],
    device: torch.device,
) -> tuple[torch.nn.Module, LinearDeltaDecoder, dict[str, Any]]:
    payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    if str(payload.get("format")) != POLICY_CHECKPOINT_VERSION:
        raise ValueError("Policy PairMLP checkpoint format differs")
    model, decoder, _ = _load_pairmlp(
        checkpoint_path=checkpoint,
        direct=direct,
        transition_view_names=transition_view_names,
    )
    model = model.to(device)
    decoder = decoder.to(device)
    model.eval()
    decoder.eval()
    return model, decoder, payload


def _evaluate_control(
    *,
    name: str,
    model: torch.nn.Module,
    decoder: LinearDeltaDecoder,
    rows: Sequence[dict[str, Any]],
    gt_rows: Sequence[dict[str, Any]],
    representations: Mapping[str, Any],
    backend: HFQwenBackend,
) -> list[dict[str, Any]]:
    pair_ids = [str(row["pair_id"]) for row in rows]
    state_indices, transition_indices = _pair_indices(rows, representations)
    states = representations["state_values"][state_indices]
    transitions = representations["transition_values"][transition_indices]
    latents = _predict_latents(
        name=PAIRMLP_NAME,
        model=model,
        state_values=states,
        transition_values=transitions,
        pair_ids=pair_ids,
        control=name,
        device=backend.device,
    )
    base_norms = _precompute_direct_base_norms(
        backend=backend, rows=rows, device=backend.device, k=4
    ).to(backend.device)
    output = []
    for index, row in enumerate(rows):
        with torch.no_grad():
            delta, ratio, _ = _applied_delta(
                decoder=decoder,
                z=latents[index : index + 1],
                base_norms=base_norms[index : index + 1],
            )
            batch = _collate([row], device=backend.device, k=4)
            student = _student_forward(
                backend=backend, batch=batch, delta=delta, prefix_enabled=False
            )
            kl, terms = _policy_loss(student["target_logits"], row["response_cache"])
            gt_batch = _collate([gt_rows[index]], device=backend.device, k=4)
            gt_student = _student_forward(
                backend=backend, batch=gt_batch, delta=delta, prefix_enabled=False
            )
        maximum_ratio = float(ratio["maximum_ratio"].cpu())
        if maximum_ratio > 1.0001:
            raise RuntimeError("Policy evaluation ratio exceeds 1.0")
        output.append(
            {
                "format": "behavioral_policy_teacher_forced_row_7dg3_v1",
                "control": name,
                "pair_id": str(row["pair_id"]),
                "state_example_id": str(row["state_example_id"]),
                "state_task_id": str(row["state_task_id"]),
                "transition_id": str(row["transition_id"]),
                "cell": str(row["cell"]),
                "policy_kl": float(kl.cpu()),
                "teacher_token_nll": float(terms["teacher_token_ce"].cpu()),
                "teacher_token_top1_accuracy": float(terms["top1"].cpu()),
                "ground_truth_nll": float(gt_student["loss"].cpu()),
                "maximum_ratio": maximum_ratio,
            }
        )
    return output


def _evaluate(
    *,
    cfg: Any,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    policy: Mapping[str, Any],
    replay: Mapping[str, Any],
    artifact_dir: Path,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    paths = _paths(direct, g3, artifact_dir)
    _require_paths(paths, ("checkpoint", "training_summary", "teacher_summary"))
    backend = _build_backend_from_generation(replay["causal_audit"]["generation"])
    manifest, _, policy_by_id, gt_by_id = _load_training_data(
        backend=backend,
        cfg=cfg,
        direct=direct,
        g3=g3,
        artifact_dir=artifact_dir,
    )
    representations = _load_representations(paths, backend.device)
    model, decoder, payload = _load_policy_model(
        checkpoint=paths["checkpoint"],
        direct=direct,
        transition_view_names=representations["transition_view_names"],
        device=backend.device,
    )
    if int(payload["completed_rounds"]) != int(policy["training"]["updates_per_pair"]):
        raise ValueError("Policy checkpoint update count differs")
    controls = tuple(str(value) for value in policy["evaluation"]["controls"])
    results: dict[str, Any] = {}
    root = paths["root"] / "evaluation"
    for cell, pairs in manifest["evaluation_pairs"].items():
        rows = [policy_by_id[str(pair["pair_id"])] for pair in pairs]
        gt_rows = [gt_by_id[str(pair["pair_id"])] for pair in pairs]
        control_results = {}
        for control in controls:
            evaluated = _evaluate_control(
                name=control,
                model=model,
                decoder=decoder,
                rows=rows,
                gt_rows=gt_rows,
                representations=representations,
                backend=backend,
            )
            path = root / cell / f"{control}_rows.jsonl"
            write_jsonl(path, evaluated)
            control_results[control] = {
                **summarize_policy_rows(evaluated),
                "rows_path": str(path),
                "rows_sha256": sha256_file(path),
            }
        results[cell] = {
            "controls": control_results,
            "diagnostics": policy_evaluation_diagnostics(control_results),
        }
        attempt.progress(
            status="policy_teacher_forced_evaluation",
            completed_cell=cell,
            latest_validated_checkpoint=str(root / cell),
        )
        print(
            f"policy eval {cell} correct_kl={control_results['correct']['policy_kl']:.6f}",
            flush=True,
        )
    finite = all(bool(value["diagnostics"]["finite"]) for value in results.values())
    summary = {
        "format": "behavioral_policy_teacher_forced_summary_7dg3_v1",
        "global_seed": GLOBAL_SEED,
        "checkpoint_sha256": sha256_file(paths["checkpoint"]),
        "model_sha256": module_state_sha256(model),
        "decoder_sha256": module_state_sha256(decoder),
        "cells": results,
        "finite": finite,
        "student_prompt_contains_raw_transition": False,
        "qwen_frozen": True,
        "one_step_authorized": finite,
        "passed": finite,
    }
    atomic_write_json(paths["evaluation_summary"], summary)
    atomic_write_text(
        paths["root"] / "teacher_forced_report.md",
        "\n".join(
            [
                "# Behavioral-policy PairMLP teacher-forced validation",
                "",
                f"- global seed: `{GLOBAL_SEED}`",
                f"- checkpoint: `{summary['checkpoint_sha256']}`",
                f"- finite/infrastructure-valid: `{str(finite).lower()}`",
                *[
                    f"- {cell}: correct KL={value['controls']['correct']['policy_kl']:.6f}, "
                    f"zero reduction={value['diagnostics']['correct_minus_zero_policy_kl_reduction']:.6f}, "
                    f"transition-shuffle reduction={value['diagnostics']['correct_minus_transition_shuffle_policy_kl_reduction']:.6f}"
                    for cell, value in results.items()
                ],
                "",
            ]
        ),
    )
    return summary


def _one_step_preflight(
    *,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    policy: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    paths = _paths(direct, g3, artifact_dir)
    _require_paths(
        paths,
        (
            "checkpoint",
            "evaluation_summary",
            "parent_g3_manifest",
            "state_cache",
            "transition_cache",
        ),
    )
    evaluation = _json(paths["evaluation_summary"])
    if not bool(evaluation["one_step_authorized"]):
        raise RuntimeError("Policy teacher-forced infrastructure did not authorize one-step")
    frozen = _json(paths["parent_g3_manifest"])
    checkpoint_provenance = {
        "checkpoint_sha256": sha256_file(paths["checkpoint"]),
        "model_sha256": str(evaluation["model_sha256"]),
        "decoder_sha256": str(evaluation["decoder_sha256"]),
        "training_summary_sha256": sha256_file(paths["training_summary"]),
    }
    manifest = build_policy_behavior_manifest(
        frozen,
        checkpoint_provenance=checkpoint_provenance,
        seed=GLOBAL_SEED,
    )
    checks = {
        "states": int(manifest["state_count"]) == 45,
        "conditions": int(manifest["condition_count"]) == 135,
        "names": set(manifest["condition_name_counts"]) == set(POLICY_CONDITIONS),
        "each_45": set(manifest["condition_name_counts"].values()) == {45},
        "raw_absent": all(
            not bool(row["student_prompt_contains_raw_transition"])
            for row in manifest["conditions"]
        ),
    }
    if not all(checks.values()):
        raise ValueError(f"Policy one-step manifest failed: {checks}")
    atomic_write_json(paths["behavior_manifest"], manifest)
    state_cache = torch.load(paths["state_cache"], map_location="cpu", weights_only=False)
    transition_cache = torch.load(paths["transition_cache"], map_location="cpu", weights_only=False)
    model, _, payload = _load_policy_model(
        checkpoint=paths["checkpoint"],
        direct=direct,
        transition_view_names=transition_cache["view_names"],
        device=torch.device("cpu"),
    )
    state_position = {str(value): index for index, value in enumerate(state_cache["ordered_ids"])}
    transition_position = {
        str(value): index for index, value in enumerate(transition_cache["ordered_ids"])
    }
    state_values = state_cache["representations"]["final_layer"].to(torch.float32)
    transition_values = transition_cache["representations"]["final_layer"].to(torch.float32)
    latents = []
    with torch.no_grad():
        for condition in manifest["conditions"]:
            latents.append(
                model(
                    state_values[state_position[str(condition["program_state_id"])]].unsqueeze(0),
                    transition_values[
                        transition_position[str(condition["program_transition_id"])]
                    ].unsqueeze(0),
                ).squeeze(0)
            )
    latent_payload = {
        "format": "behavioral_policy_pairmlp_one_step_latents_7dg3_v1",
        "global_seed": GLOBAL_SEED,
        "condition_keys": [str(row["condition_key"]) for row in manifest["conditions"]],
        "latents": torch.stack(latents),
        "checkpoint_sha256": sha256_file(paths["checkpoint"]),
        "model_sha256": str(payload["model_sha256"]),
        "decoder_sha256": str(payload["decoder_sha256"]),
        "student_prompt_contains_raw_transition": False,
    }
    atomic_torch_save(latent_payload, paths["behavior_latents"])
    rate = float(policy["runtime"]["teacher_generation_seconds"]["expected"])
    report = {
        "format": "behavioral_policy_pairmlp_one_step_preflight_7dg3_v1",
        "global_seed": GLOBAL_SEED,
        "checks": checks,
        "state_count": 45,
        "condition_count": 135,
        "qwen_generation_count": 135,
        "appworld_reconstruction_execution_count": 135,
        "expected_h100_hours": 135 * rate / 3600.0,
        "manifest_sha256": sha256_file(paths["behavior_manifest"]),
        "latents_sha256": sha256_file(paths["behavior_latents"]),
        "passed": True,
    }
    atomic_write_json(paths["behavior_preflight"], report)
    return report


def _one_step(
    *,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    policy: Mapping[str, Any],
    replay: Mapping[str, Any],
    artifact_dir: Path,
    attempt: AttemptLedger,
    attempt_id: str,
) -> dict[str, Any]:
    from scripts.run_state_conditioned_program_fast_one_step_7df import (
        _build_injector,
    )

    paths = _paths(direct, g3, artifact_dir)
    _require_paths(
        paths,
        (
            "behavior_preflight",
            "behavior_manifest",
            "behavior_latents",
            "checkpoint",
            "decisions",
            "memories",
            "semantic_module",
            "bridge_script",
        ),
    )
    if not bool(_json(paths["behavior_preflight"])["passed"]):
        raise RuntimeError("Policy one-step preflight did not pass")
    manifest = _json(paths["behavior_manifest"])
    latent_payload = torch.load(paths["behavior_latents"], map_location="cpu", weights_only=False)
    conditions = list(manifest["conditions"])
    if latent_payload["condition_keys"] != [str(row["condition_key"]) for row in conditions]:
        raise ValueError("Policy one-step latent order differs")
    backend = _build_backend_from_generation(replay["causal_audit"]["generation"])
    injector, decoder, decoder_sha256 = _build_injector(
        backend=backend,
        decoder_path=paths["checkpoint"],
    )
    if decoder_sha256 != str(latent_payload["decoder_sha256"]):
        raise ValueError("Live policy decoder hash differs")
    examples = _examples_by_state(load_decision_examples(paths["decisions"]))
    records = _records_by_task(load_memory_records(paths["memories"]))
    positions = {key: index for index, key in enumerate(latent_payload["condition_keys"])}
    output_dir = paths["root"] / "one_step/condition_outputs"
    completed = []
    resumed = 0
    started = time.perf_counter()
    for ordinal, condition in enumerate(conditions, start=1):
        key = str(condition["condition_key"])
        row, reused = _run_condition(
            condition=condition,
            z=latent_payload["latents"][positions[key]],
            output_path=output_dir / condition_checkpoint_name(key),
            stderr_path=paths["root"] / f"one_step/worker_logs/formal/{key}.stderr.log",
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
            status="policy_pairmlp_one_step",
            completed_conditions=len(completed),
            total_conditions=len(conditions),
            latest_validated_checkpoint=str(output_dir / condition_checkpoint_name(key)),
        )
        print(f"policy one-step {len(completed)}/{len(conditions)}", flush=True)
    summary = {
        "format": "behavioral_policy_pairmlp_generation_summary_7dg3_v1",
        "global_seed": GLOBAL_SEED,
        "condition_count": len(completed),
        "unique_condition_count": len({str(row["condition_key"]) for row in completed}),
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
            not bool(row["live_worker"]["same_world_execution"]) for row in completed
        ),
        "execution_exception_count": sum(
            row["live_worker"]["execution_exception"] is not None for row in completed
        ),
        "checkpoint_sha256": sha256_file(paths["checkpoint"]),
        "decoder_sha256": decoder_sha256,
        "qwen_frozen": True,
        "passed": len(completed) == len(conditions) == 135
        and len({str(row["condition_key"]) for row in completed}) == 135
        and all(row["live_worker"]["same_world_execution"] for row in completed)
        and all(row["live_worker"]["same_python_namespace"] for row in completed),
    }
    atomic_write_json(paths["behavior_generation"], summary)
    return summary


def _positive_task_count(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[int, dict[str, Any]]:
    by_task: dict[str, dict[str, dict[str, Mapping[str, Any]]]] = {}
    for row in rows:
        task = str(row["state_task_id"])
        condition = str(row["condition_name"])
        state = str(row["state_example_id"])
        by_task.setdefault(task, {}).setdefault(condition, {})[state] = row
    report = {}
    for task, conditions in sorted(by_task.items()):
        p1 = conditions.get(POLICY_CONDITIONS[0], {})
        c0 = conditions.get("C0_bare", {})
        shared = sorted(set(p1) & set(c0))
        if not shared:
            continue
        signature = statistics.fmean(
            float(p1[state]["metrics"]["canonical_procedural_signature_match"])
            - float(c0[state]["metrics"]["canonical_procedural_signature_match"])
            for state in shared
        )
        successor = statistics.fmean(
            float(p1[state]["metrics"]["semantic_successor_match"])
            - float(c0[state]["metrics"]["semantic_successor_match"])
            for state in shared
        )
        report[task] = {
            "paired_state_count": len(shared),
            "action_signature_difference": signature,
            "semantic_successor_difference": successor,
            "positive_relative_behavior": signature > 0.0 or successor > 0.0,
        }
    return sum(bool(value["positive_relative_behavior"]) for value in report.values()), report


def _analyze(
    *,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    policy: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    paths = _paths(direct, g3, artifact_dir)
    generation = _json(paths["behavior_generation"])
    if not bool(generation["passed"]):
        raise RuntimeError("Policy one-step infrastructure is invalid")
    output_dir = paths["root"] / "one_step/condition_outputs"
    policy_rows = [_json(path) for path in sorted(output_dir.glob("*.json"))]
    if len(policy_rows) != 135:
        raise ValueError(f"Expected 135 policy one-step rows, found {len(policy_rows)}")
    c0 = _load_parent_rows(paths["parent_c0_outputs"], "C0_bare")
    f3 = _load_parent_rows(paths["parent_f3_outputs"], "F3_deployment_e_field_raw")
    combined = policy_rows + c0 + f3
    primary = [row for row in combined if str(row["audit_stratum"]) in {"A", "B"}]
    if len({str(row["state_example_id"]) for row in primary}) != 32:
        raise ValueError("Policy one-step primary state count differs")
    p1, p2, p3 = POLICY_CONDITIONS
    pairs = ((p1, "C0_bare"), (p1, "F3_deployment_e_field_raw"), (p1, p2), (p1, p3))
    comparisons = {
        f"{left}_minus_{right}": comparison_set(
            primary,
            left=left,
            right=right,
            bootstrap_samples=int(g3["bootstrap_samples"]),
            seed=GLOBAL_SEED,
            per_metric_seed_offset=False,
        )
        for left, right in pairs
    }
    f3_c0 = comparison_set(
        primary,
        left="F3_deployment_e_field_raw",
        right="C0_bare",
        bootstrap_samples=int(g3["bootstrap_samples"]),
        seed=GLOBAL_SEED,
        per_metric_seed_offset=False,
    )
    positive_count, task_details = _positive_task_count(primary)
    gate = pair_behavior_gate(
        p1_minus_c0=comparisons[f"{p1}_minus_C0_bare"],
        p1_minus_p2=comparisons[f"{p1}_minus_{p2}"],
        p1_minus_p3=comparisons[f"{p1}_minus_{p3}"],
        f3_minus_c0=f3_c0,
        positive_task_count=positive_count,
        material_degradation_tolerance=float(g3["one_step"]["material_degradation_tolerance"]),
    )
    passed = bool(gate["passed"])
    branch = (
        "behavioral_policy_distillation_pairmlp_passed"
        if passed
        else "behavioral_policy_distillation_pairmlp_failed"
    )
    summary = {
        "format": "behavioral_policy_pairmlp_analysis_7dg3_v1",
        "run_uuid": str(policy["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "condition_metrics_all": condition_summary(combined),
        "condition_metrics_primary": condition_summary(primary),
        "per_task": per_task_summary(combined),
        "comparisons_primary": comparisons,
        "f3_raw_minus_c0": f3_c0,
        "positive_task_count": positive_count,
        "positive_task_details": task_details,
        "gate": gate,
        "decision_branch": branch,
        "policy_pairmlp_one_step_passed": passed,
        "r64_started": False,
        "actual_qwen_h100_hours": float(generation["qwen_generation_seconds"]) / 3600.0,
        "actual_one_step_wall_hours": float(generation["elapsed_seconds"]) / 3600.0,
    }
    atomic_write_json(paths["behavior_analysis"], summary)
    metrics = summary["condition_metrics_primary"]
    lines = [
        "# EXP-025D-G3 behavioral-policy PairMLP one-step audit",
        "",
        f"- global seed: `{GLOBAL_SEED}`",
        f"- formal conditions: `{generation['condition_count']}/135`",
        f"- positive tasks: `{positive_count}/9`",
        f"- decision branch: `{branch}`",
        "",
        "## Primary condition means",
        "",
    ]
    for name in ("C0_bare", "F3_deployment_e_field_raw", p1, p2, p3):
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
    atomic_write_text(paths["root"] / "one_step_report.md", "\n".join(lines))
    return summary


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    replay_cfg = load_config(args.replay_config)
    direct = cfg.raw["stage_c_7dg"]
    g3 = cfg.raw["stage_c_7dg3"]
    policy = cfg.raw["stage_c_7dg3_policy"]
    replay = replay_cfg.raw["stage_c_7b"]
    require_global_seed(int(policy["global_seed"]))
    seed_everything(GLOBAL_SEED)
    if os.name != "nt" and not os.path.ismount(Path(str(g3["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    paths = _paths(direct, g3, args.artifact_dir)
    data_hashes = {
        name: sha256_file(path)
        for name, path in {
            "config": args.config,
            "replay_config": args.replay_config,
            "parent_pairmlp_checkpoint": paths["parent_pairmlp_checkpoint"],
            "parent_g3_analysis": paths["parent_g3_analysis"],
            "selector": paths["selector"],
        }.items()
        if path.exists()
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(policy["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"behavioral_policy_distillation_{args.phase}",
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
        heartbeat_interval_s=float(policy["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "preflight":
            result = _preflight(
                direct=direct,
                g3=g3,
                policy=policy,
                artifact_dir=args.artifact_dir,
            )
        elif args.phase == "teacher":
            result = _teacher_cache(
                cfg=cfg,
                direct=direct,
                g3=g3,
                policy=policy,
                replay=replay,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
            )
        elif args.phase == "train":
            result = _train(
                cfg=cfg,
                direct=direct,
                g3=g3,
                policy=policy,
                replay=replay,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
            )
        elif args.phase == "evaluate":
            result = _evaluate(
                cfg=cfg,
                direct=direct,
                g3=g3,
                policy=policy,
                replay=replay,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
            )
        elif args.phase == "one_step_preflight":
            result = _one_step_preflight(
                direct=direct,
                g3=g3,
                policy=policy,
                artifact_dir=args.artifact_dir,
            )
        elif args.phase == "one_step":
            result = _one_step(
                direct=direct,
                g3=g3,
                policy=policy,
                replay=replay,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
                attempt_id=args.attempt_id,
            )
        else:
            result = _analyze(
                direct=direct,
                g3=g3,
                policy=policy,
                artifact_dir=args.artifact_dir,
            )
        checkpoints = {
            "preflight": paths["preflight"],
            "teacher": paths["teacher_summary"],
            "train": paths["training_summary"],
            "evaluate": paths["evaluation_summary"],
            "one_step_preflight": paths["behavior_preflight"],
            "one_step": paths["behavior_generation"],
            "analyze": paths["behavior_analysis"],
        }
        attempt.progress(
            status=f"behavioral_policy_distillation_{args.phase}_completed",
            latest_validated_checkpoint=str(checkpoints[args.phase]),
            result_passed=bool(result.get("passed", True)),
        )
        print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
