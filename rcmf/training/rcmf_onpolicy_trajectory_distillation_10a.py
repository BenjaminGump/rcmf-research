"""Contracts for EXP-032A on-policy trajectory-union distillation."""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import re
from typing import Any

from torch import nn

from rcmf.training.rcmf_joint_full_bank_9a import (
    AlignedTransitionWriter,
    StandardFieldCrossAttentionReader,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256


GLOBAL_SEED = 25101
UNION_FORMAT = "rcmf_onpolicy_trajectory_union_10a_v1"
AUGMENTATION_FORMAT = "rcmf_bank_robustness_augmentation_10a_v1"
CHECKPOINT_FORMAT = "rcmf_onpolicy_trajectory_checkpoint_10a_v1"
TASK_CLASSES = (
    "bare_only_success",
    "rcmf_only_success",
    "both_success",
    "neither_success",
)


def classify_task(*, bare_success: bool, rcmf_success: bool) -> str:
    if bare_success and not rcmf_success:
        return "bare_only_success"
    if rcmf_success and not bare_success:
        return "rcmf_only_success"
    if bare_success and rcmf_success:
        return "both_success"
    return "neither_success"


def trajectory_quality(row: Mapping[str, Any]) -> tuple[int, int, int, int]:
    """Lower is better under the locked both-success lexicographic rule."""
    counts = row.get("counts", {})
    usage = row.get("usage", {})
    return (
        int(counts.get("execution_exception", 0)),
        int(row.get("strict_no_progress_loop_count", 0)),
        int(row["step_count"]),
        int(usage.get("completion_tokens", 0)),
    )


def successful_trajectory_weights(
    *, bare: Mapping[str, Any], rcmf: Mapping[str, Any]
) -> list[dict[str, Any]]:
    task_class = classify_task(
        bare_success=bool(bare["success"]), rcmf_success=bool(rcmf["success"])
    )
    if task_class == "bare_only_success":
        return [{"condition": "T0", "weight": 1.0, "role": "preservation"}]
    if task_class == "rcmf_only_success":
        return [{"condition": "T1", "weight": 1.0, "role": "memory_benefit"}]
    if task_class == "neither_success":
        return []
    bare_quality = trajectory_quality(bare)
    rcmf_quality = trajectory_quality(rcmf)
    primary = "T0" if bare_quality < rcmf_quality else "T1"
    alternate = "T1" if primary == "T0" else "T0"
    return [
        {"condition": primary, "weight": 1.0, "role": "both_primary"},
        {"condition": alternate, "weight": 0.5, "role": "both_alternate"},
    ]


def _normalized_code(code: str) -> str:
    try:
        return ast.dump(ast.parse(code), annotate_fields=True, include_attributes=False)
    except (SyntaxError, ValueError, TypeError):
        return re.sub(r"\s+", " ", code).strip()


def _normalized_observation(step: Mapping[str, Any]) -> str:
    locked = step.get("locked_normalized_observation")
    value = str(locked if locked is not None else step["complete_environment_observation"])
    try:
        return json.dumps(json.loads(value), sort_keys=True, separators=(",", ":"))
    except (json.JSONDecodeError, TypeError):
        return re.sub(r"\s+", " ", value).strip()


def strict_no_progress_loops(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return failed runs of >=3 identical actions, observations, and states."""
    if bool(result["success"]):
        return []
    steps = list(result["steps"])
    runs: list[dict[str, Any]] = []
    start = 0
    while start < len(steps):
        first = steps[start]
        action = _normalized_code(str(first["exact_executed_code"]))
        observation = _normalized_observation(first)
        end = start + 1
        while end < len(steps):
            row = steps[end]
            if _normalized_code(str(row["exact_executed_code"])) != action:
                break
            if _normalized_observation(row) != observation:
                break
            end += 1
        if end - start >= 3:
            selected = steps[start:end]
            if all(
                str(row.get("state_fingerprint_before", ""))
                == str(row.get("state_fingerprint_after", ""))
                and str(row.get("state_fingerprint_before", "")) != ""
                for row in selected
            ):
                runs.append(
                    {
                        "start_step": int(selected[0]["step_id"]),
                        "end_step": int(selected[-1]["step_id"]),
                        "repetition_count": len(selected),
                        "action_sha256": hashlib.sha256(action.encode("utf-8")).hexdigest(),
                        "observation_sha256": hashlib.sha256(
                            observation.encode("utf-8")
                        ).hexdigest(),
                    }
                )
        start = end
    return runs


def first_common_history_preference(
    *, bare: Mapping[str, Any], rcmf: Mapping[str, Any]
) -> dict[str, Any] | None:
    if bool(bare["success"]) == bool(rcmf["success"]):
        return None
    preferred_condition = "T0" if bool(bare["success"]) else "T1"
    for bare_step, rcmf_step in zip(bare["steps"], rcmf["steps"]):
        if bare_step["complete_trajectory_so_far"] != rcmf_step["complete_trajectory_so_far"]:
            return None
        bare_action = str(bare_step["exact_executed_code"])
        rcmf_action = str(rcmf_step["exact_executed_code"])
        if _normalized_code(bare_action) != _normalized_code(rcmf_action):
            preferred = bare_step if preferred_condition == "T0" else rcmf_step
            rejected = rcmf_step if preferred_condition == "T0" else bare_step
            return {
                "step_id": int(preferred["step_id"]),
                "preferred_condition": preferred_condition,
                "rejected_condition": "T1" if preferred_condition == "T0" else "T0",
                "common_history_sha256": canonical_sha256(
                    preferred["complete_trajectory_so_far"]
                ),
                "preferred_response_sha256": hashlib.sha256(
                    str(preferred["raw_model_response"]).encode("utf-8")
                ).hexdigest(),
                "rejected_response_sha256": hashlib.sha256(
                    str(rejected["raw_model_response"]).encode("utf-8")
                ).hexdigest(),
            }
    return None


def _unit_hash_fraction(unit_id: str, namespace: str) -> float:
    digest = hashlib.sha256(
        f"{GLOBAL_SEED}:{namespace}:{unit_id}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def deterministic_bank_augmentation(
    *,
    unit_id: str,
    query_task_id: str,
    parent_task_ids: Sequence[str],
    fraction: float = 0.25,
    removal_fraction: float = 0.10,
) -> dict[str, Any]:
    if not 0.0 <= fraction <= 1.0 or not 0.0 < removal_fraction < 1.0:
        raise ValueError("Invalid bank augmentation fractions")
    eligible = sorted({str(value) for value in parent_task_ids} - {query_task_id})
    active = _unit_hash_fraction(unit_id, "bank-augmentation-active") < fraction
    remove_count = max(1, math.ceil(removal_fraction * len(eligible))) if active else 0
    ordered = sorted(
        eligible,
        key=lambda task_id: (
            hashlib.sha256(
                f"{GLOBAL_SEED}:bank-augmentation-parent:{unit_id}:{task_id}".encode(
                    "utf-8"
                )
            ).hexdigest(),
            task_id,
        ),
    )
    removed = ordered[:remove_count]
    return {
        "format": AUGMENTATION_FORMAT,
        "unit_id": unit_id,
        "active": active,
        "query_task_id": query_task_id,
        "eligible_parent_count": len(eligible),
        "removed_parent_task_ids": removed,
        "removed_parent_count": len(removed),
        "selection_uses_outcomes": False,
        "global_seed": GLOBAL_SEED,
    }


def balance_union_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Equalize preservation/benefit totals and normalize auxiliary groups."""
    result = [dict(row) for row in rows]
    groups = Counter(str(row["balance_group"]) for row in result)
    for required in ("preservation", "memory_benefit"):
        if required not in groups:
            continue
        denominator = sum(
            float(item["sample_weight"])
            for item in result
            if str(item["balance_group"]) == required
        )
        for row in result:
            if str(row["balance_group"]) == required:
                row["balanced_weight"] = float(row["sample_weight"]) / denominator
    for group in ("both_success", "neither_auxiliary"):
        members = [row for row in result if str(row["balance_group"]) == group]
        if members:
            total = sum(float(row["sample_weight"]) for row in members)
            for row in members:
                row["balanced_weight"] = float(row["sample_weight"]) / total
    if any("balanced_weight" not in row for row in result):
        raise ValueError("Every union row must receive a balanced weight")
    return result


def configure_reader_only_trainables(
    *, writer: AlignedTransitionWriter, reader: StandardFieldCrossAttentionReader
) -> list[nn.Parameter]:
    for parameter in writer.parameters():
        parameter.requires_grad_(False)
    for parameter in reader.parameters():
        parameter.requires_grad_(False)
    parameters = []
    for adapter in reader.adapters.values():
        adapter.output.weight.requires_grad_(True)
        parameters.append(adapter.output.weight)
    return parameters


def configure_writer_last_layer_trainables(
    *, writer: AlignedTransitionWriter, reader: StandardFieldCrossAttentionReader
) -> tuple[list[nn.Parameter], list[nn.Parameter]]:
    reader_parameters = configure_reader_only_trainables(writer=writer, reader=reader)
    writer_parameters: list[nn.Parameter] = []
    for section in writer.writers.values():
        section.output.weight.requires_grad_(True)
        section.output.bias.requires_grad_(True)
        writer_parameters.extend((section.output.weight, section.output.bias))
    return reader_parameters, writer_parameters


def trainable_parameter_names(module: nn.Module) -> list[str]:
    return sorted(name for name, parameter in module.named_parameters() if parameter.requires_grad)


def candidate_eligibility(
    *,
    correct_success_ids: Sequence[str],
    shuffle_success_ids: Sequence[str],
    original_success_ids: Sequence[str],
    correct_loop_count: int,
    original_loop_count: int,
    infrastructure_valid: bool,
) -> dict[str, Any]:
    correct = set(correct_success_ids)
    shuffle = set(shuffle_success_ids)
    original = set(original_success_ids)
    lost = sorted(original - correct)
    loop_limit = 1.2 * original_loop_count
    loop_ok = correct_loop_count <= loop_limit if original_loop_count else correct_loop_count == 0
    checks = {
        "correct_at_least_original": len(correct) >= len(original),
        "correct_above_shuffle": len(correct) > len(shuffle),
        "loses_at_most_one_original_success": len(lost) <= 1,
        "loop_count_within_limit": loop_ok,
        "infrastructure_valid": bool(infrastructure_valid),
    }
    return {
        "eligible": all(checks.values()),
        "checks": checks,
        "lost_original_success_ids": lost,
        "one_item_sensitive": abs(len(correct) - len(original)) == 1,
    }


def select_final_candidate(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    eligible = [dict(row) for row in candidates if bool(row["eligible"])]
    if not eligible:
        return None
    eligible.sort(
        key=lambda row: (
            -int(row["correct_success_count"]),
            -int(row["correct_minus_shuffle"]),
            -int(row["retained_original_success_count"]),
            int(row["no_progress_loop_count"]),
            int(row["total_steps"]),
            0 if str(row["stage"]) == "reader_only" else 1,
        )
    )
    return eligible[0]


def first37_decision(metrics: Mapping[str, Any]) -> dict[str, Any]:
    n1 = int(metrics["N1"])
    n2 = int(metrics["N2"])
    f0 = int(metrics["F0"])
    retained_gains = int(metrics["retained_original_gain_count"])
    retained_successes = int(metrics["retained_original_success_count"])
    gain_families = set(metrics["gain_families"])
    recovery_ok = int(metrics["recovered_original_loss_count"]) >= 2 or int(
        metrics["equivalent_new_gain_family_count"]
    ) >= 2
    proceed = (
        n1 >= 10
        and n1 >= f0 + 2
        and n1 >= n2 + 2
        and retained_gains >= 5
        and gain_families
        >= {"cross_app_import", "spotify_state_machine", "exact_set_migration"}
        and retained_successes >= 2
        and recovery_ok
        and bool(metrics["complexity_contract_valid"])
    )
    stop = (
        n1 <= n2
        or n1 < f0 - 1
        or retained_gains <= 4
        or retained_successes < 2
        or bool(metrics["no_progress_loops_materially_increased"])
        or not bool(metrics["complexity_contract_valid"])
    )
    if proceed:
        return {"decision": "trajectory_union_distillation_preliminary_positive"}
    if stop:
        return {"decision": "trajectory_union_distillation_stop"}
    return {"decision": "trajectory_union_distillation_inconclusive"}
