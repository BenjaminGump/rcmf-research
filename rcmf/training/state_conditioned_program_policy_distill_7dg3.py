from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import math
from typing import Any

import torch
from torch import Tensor
import torch.nn.functional as F

from rcmf.training.state_conditioned_program_7d import canonical_sha256, stable_key


GLOBAL_SEED = 25101
POLICY_CONDITIONS = (
    "P1_policy_pairmlp_correct",
    "P2_policy_pairmlp_shuffled_transition",
    "P3_policy_pairmlp_shuffled_state",
)


def _coverage_select(
    rows: Sequence[Mapping[str, Any]],
    *,
    count: int,
    namespace: str,
    seed: int = GLOBAL_SEED,
) -> list[dict[str, Any]]:
    if count <= 0 or count > len(rows):
        raise ValueError(f"Invalid requested count {count} for {len(rows)} rows")
    remaining = {str(row["pair_id"]): dict(row) for row in rows}
    selected: list[dict[str, Any]] = []
    task_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    parent_counts: Counter[str] = Counter()
    transition_counts: Counter[str] = Counter()
    class_counts: Counter[str] = Counter()
    while len(selected) < count:
        ranked: list[tuple[tuple[Any, ...], str, dict[str, Any]]] = []
        for pair_id, row in remaining.items():
            task = str(row["state_task_id"])
            role = str(row.get("pair_role", "unknown"))
            parent = str(row["transition_parent_id"])
            transition = str(row["transition_id"])
            signature = str(row.get("signature_class_id", "unknown"))
            score = (
                int(task_counts[task] == 0),
                int(role_counts[role] == 0),
                int(parent_counts[parent] == 0),
                int(class_counts[signature] == 0),
                int(transition_counts[transition] == 0),
                -task_counts[task],
                -role_counts[role],
                -parent_counts[parent],
                -class_counts[signature],
                stable_key(seed, namespace, pair_id),
            )
            ranked.append((score, pair_id, row))
        _, pair_id, chosen = max(ranked, key=lambda item: item[0])
        selected.append(chosen)
        task_counts[str(chosen["state_task_id"])] += 1
        role_counts[str(chosen.get("pair_role", "unknown"))] += 1
        parent_counts[str(chosen["transition_parent_id"])] += 1
        transition_counts[str(chosen["transition_id"])] += 1
        class_counts[str(chosen.get("signature_class_id", "unknown"))] += 1
        del remaining[pair_id]
    return selected


def build_policy_pair_manifest(
    manifests: Mapping[str, Sequence[Mapping[str, Any]]],
    split: Mapping[str, Any],
    *,
    training_count: int,
    evaluation_counts: Mapping[str, int],
    context_limit: int,
    max_new_tokens: int,
    seed: int = GLOBAL_SEED,
) -> dict[str, Any]:
    """Freeze context-feasible policy-teacher pairs before Qwen is loaded."""
    if int(seed) != GLOBAL_SEED:
        raise ValueError(f"Policy distillation requires GLOBAL_SEED={GLOBAL_SEED}")
    a_rows = [dict(row) for row in manifests["A"]]
    train_candidates = [a_rows[int(index)] for index in split["train_indices"]]
    a_validation = [a_rows[int(index)] for index in split["validation_indices"]]

    def feasible(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in rows
            if bool(row.get("valid_for_teacher_cache", False))
            and not bool(row.get("truncated", False))
            and int(row["teacher_prompt_tokens"]) + int(max_new_tokens) <= int(context_limit)
        ]

    training_pool = feasible(train_candidates)
    training = _coverage_select(
        training_pool,
        count=int(training_count),
        namespace="policy-distillation-train",
        seed=seed,
    )
    evaluation_sources = {
        "A_validation": a_validation,
        "B": manifests["B"],
        "C": manifests["C"],
        "D": manifests["D"],
        "E": manifests["E"],
    }
    evaluation: dict[str, list[dict[str, Any]]] = {}
    feasibility: dict[str, Any] = {
        "A_training": {
            "logical": len(train_candidates),
            "context_feasible": len(training_pool),
            "selected": len(training),
        }
    }
    for cell, source_rows in evaluation_sources.items():
        pool = feasible(source_rows)
        count = int(evaluation_counts[cell])
        evaluation[cell] = _coverage_select(
            pool,
            count=count,
            namespace=f"policy-distillation-eval-{cell}",
            seed=seed,
        )
        feasibility[cell] = {
            "logical": len(source_rows),
            "context_feasible": len(pool),
            "selected": len(evaluation[cell]),
        }

    all_rows = training + [row for values in evaluation.values() for row in values]
    unique = {str(row["pair_id"]): row for row in all_rows}
    train_tasks = {str(row["state_task_id"]) for row in training}
    expected_train_tasks = {str(value) for value in split["train_task_ids"]}
    if train_tasks != expected_train_tasks:
        missing = sorted(expected_train_tasks - train_tasks)
        raise ValueError(f"Policy training manifest misses A-train tasks: {missing}")
    train_ids = {str(row["pair_id"]) for row in training}
    eval_ids = {str(row["pair_id"]) for values in evaluation.values() for row in values}
    if train_ids & eval_ids:
        raise ValueError("Policy training and evaluation pair IDs overlap")

    payload = {
        "format": "behavioral_policy_pair_manifest_7dg3_v1",
        "global_seed": GLOBAL_SEED,
        "selection_uses_model_or_behavioral_outcomes": False,
        "selection_uses_raw_nll_magnitude": False,
        "context_limit": int(context_limit),
        "max_new_tokens": int(max_new_tokens),
        "training_count": len(training),
        "training_task_count": len(train_tasks),
        "evaluation_counts": {name: len(rows) for name, rows in evaluation.items()},
        "logical_pair_count": len(all_rows),
        "unique_teacher_pair_count": len(unique),
        "feasibility": feasibility,
        "training_pairs": training,
        "evaluation_pairs": evaluation,
        "unique_pairs": [unique[key] for key in sorted(unique)],
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def sparse_policy_kl(
    student_logits: Tensor,
    teacher_positions: Sequence[Mapping[str, Any]],
) -> Tensor:
    """KL over teacher top-k tokens plus one exact residual-mass bucket."""
    if student_logits.shape[0] != len(teacher_positions):
        raise ValueError("Policy logit and teacher-position counts differ")
    losses: list[Tensor] = []
    for logits, position in zip(student_logits, teacher_positions):
        ids = torch.tensor(position["top_token_ids"], dtype=torch.long, device=logits.device)
        teacher_log = torch.tensor(
            position["top_logprobs"], dtype=torch.float64, device=logits.device
        )
        teacher_prob = teacher_log.exp()
        teacher_other = torch.tensor(
            float(position["other_probability"]),
            dtype=torch.float64,
            device=logits.device,
        ).clamp(min=0.0, max=1.0)
        student_log_all = F.log_softmax(logits.to(torch.float64), dim=-1)
        student_log = student_log_all[ids]
        student_selected_mass = student_log.exp().sum().clamp(min=0.0, max=1.0 - 1.0e-12)
        student_other_log = torch.log1p(-student_selected_mass)
        selected_kl = (teacher_prob * (teacher_log - student_log)).sum()
        other_kl = torch.where(
            teacher_other > 0.0,
            teacher_other * (teacher_other.clamp_min(1.0e-300).log() - student_other_log),
            torch.zeros_like(teacher_other),
        )
        losses.append((selected_kl + other_kl).to(torch.float32))
    if not losses:
        raise ValueError("Policy KL requires at least one teacher position")
    return torch.stack(losses).mean()


def summarize_policy_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("Cannot summarize empty policy evaluation rows")
    metrics = (
        "policy_kl",
        "teacher_token_nll",
        "teacher_token_top1_accuracy",
        "ground_truth_nll",
        "maximum_ratio",
    )
    return {
        "row_count": len(rows),
        **{name: sum(float(row[name]) for row in rows) / len(rows) for name in metrics},
    }


def policy_evaluation_diagnostics(
    controls: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    required = {"correct", "state_shuffle", "transition_shuffle", "zero"}
    if set(controls) != required:
        raise ValueError(f"Policy controls differ: {sorted(controls)}")
    correct = controls["correct"]
    return {
        "correct_minus_zero_policy_kl_reduction": float(controls["zero"]["policy_kl"])
        - float(correct["policy_kl"]),
        "correct_minus_state_shuffle_policy_kl_reduction": float(
            controls["state_shuffle"]["policy_kl"]
        )
        - float(correct["policy_kl"]),
        "correct_minus_transition_shuffle_policy_kl_reduction": float(
            controls["transition_shuffle"]["policy_kl"]
        )
        - float(correct["policy_kl"]),
        "finite": all(
            math.isfinite(float(summary[name]))
            for summary in controls.values()
            for name in (
                "policy_kl",
                "teacher_token_nll",
                "teacher_token_top1_accuracy",
                "ground_truth_nll",
                "maximum_ratio",
            )
        ),
    }


def build_policy_behavior_manifest(
    frozen_pair_manifest: Mapping[str, Any],
    *,
    checkpoint_provenance: Mapping[str, Any],
    seed: int = GLOBAL_SEED,
) -> dict[str, Any]:
    """Reuse G3 frozen pairings while versioning the policy-trained program."""
    if int(seed) != GLOBAL_SEED:
        raise ValueError(f"Policy behavior requires GLOBAL_SEED={GLOBAL_SEED}")
    name_map = {
        "P1_pairmlp_correct": POLICY_CONDITIONS[0],
        "P2_pairmlp_shuffled_transition": POLICY_CONDITIONS[1],
        "P3_pairmlp_shuffled_state": POLICY_CONDITIONS[2],
    }
    conditions = []
    for source in frozen_pair_manifest["conditions"]:
        old_name = str(source["condition_name"])
        if old_name not in name_map:
            raise ValueError(f"Unexpected frozen G3 condition: {old_name}")
        row = {
            **dict(source),
            "format": "policy_pair_behavior_condition_7dg3_v1",
            "condition_name": name_map[old_name],
            "program_provenance": dict(checkpoint_provenance),
            "policy_checkpoint_provenance": dict(checkpoint_provenance),
        }
        row.pop("condition_key", None)
        row["condition_key"] = canonical_sha256(row)
        conditions.append(row)
    payload = {
        "format": "policy_pair_behavior_manifest_7dg3_v1",
        "global_seed": GLOBAL_SEED,
        "state_count": len({str(row["state_example_id"]) for row in conditions}),
        "condition_count": len(conditions),
        "condition_name_counts": dict(
            sorted(Counter(row["condition_name"] for row in conditions).items())
        ),
        "frozen_pairing_manifest_sha256": str(frozen_pair_manifest["manifest_sha256"]),
        "checkpoint_provenance": dict(checkpoint_provenance),
        "conditions": conditions,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload
