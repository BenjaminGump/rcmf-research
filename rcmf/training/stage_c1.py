from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import copy
import math
import random
from typing import Any, Iterable, Sequence

import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.training.addressing_4b import (
    _pearson,
    bootstrap_metric_ci,
    distribution,
    mean_std,
    pairwise_cosine_summary,
    singular_summary,
)
from rcmf.training.addressing_only import rows_to_tensors, task_balanced_batches
from rcmf.training.signed_residual_field import SignedResidualField, train_memory_prior


STAGE_C1_RESPONSE_CACHE_VERSION = "stage_c1_best_raw_memory_response_cache_v1"
STAGE_C1_RESPONSE_SCORING_DEFINITION = "best_raw_memory_or_bare_qwen_target_top64_v1"
STAGE_C1_PROGRAM_FIELD_VERSION = "stage_c1_signed_program_field_v1"
POSITIVE_TEACHER_EPS = 0.01


def rms_normalize(value: Tensor, eps: float = 1.0e-6) -> Tensor:
    return value / torch.sqrt(value.pow(2).mean(dim=-1, keepdim=True) + eps)


def state_key(row: dict[str, Any]) -> str:
    return str(row["state_example_id"])


def pair_key(example_index: int, memory_index: int) -> str:
    return f"e{example_index}:m{memory_index}"


def load_teacher_rows(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("pair_key") or pair_key(int(row["example_index"]), int(row["candidate_memory_index"])))
        if key in output:
            raise ValueError(f"duplicate teacher row pair_key: {key}")
        output[key] = row
    return output


def split_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return (
        [row for row in rows if str(row.get("split")) == "train"],
        [row for row in rows if str(row.get("split")) == "validation"],
    )


def select_teacher_conditions(
    label_rows: list[dict[str, Any]],
    memory_bank: list[dict[str, Any]],
    teacher_rows: dict[str, dict[str, Any]],
    *,
    positive_eps: float = POSITIVE_TEACHER_EPS,
) -> list[dict[str, Any]]:
    if not memory_bank:
        raise ValueError("memory_bank must not be empty")
    memory_ids = [str(row["memory_id"]) for row in memory_bank]
    if len(memory_ids) != len(set(memory_ids)):
        raise ValueError("duplicate effective memory ids")
    output: list[dict[str, Any]] = []
    for row in label_rows:
        best_position: int | None = None
        best_utility: float | None = None
        valid_count = 0
        for position, (valid, utility) in enumerate(zip(row["valid_mask"], row["raw_utility"])):
            if not valid or utility is None:
                continue
            valid_count += 1
            value = float(utility)
            if best_utility is None or value > best_utility:
                best_utility = value
                best_position = position
        all_missing = valid_count == 0
        if all_missing:
            condition = "all_missing"
        elif best_utility is not None and best_utility > positive_eps:
            condition = "positive_teacher"
        else:
            condition = "baseline_teacher"
        best_memory: dict[str, Any] | None = None
        best_pair_key: str | None = None
        best_teacher_row: dict[str, Any] | None = None
        if best_position is not None:
            best_memory = memory_bank[best_position]
            source_pair = row["source_pair_keys"][best_position]
            best_pair_key = str(source_pair) if source_pair is not None else None
            if best_pair_key is not None:
                best_teacher_row = teacher_rows.get(best_pair_key)
                if best_teacher_row is None:
                    raise ValueError(f"missing teacher row for selected pair {best_pair_key}")
        if condition == "positive_teacher":
            if best_teacher_row is None or not bool(best_teacher_row.get("valid_for_loss")):
                raise ValueError(f"selected positive teacher row is not valid: {row['state_example_id']}")
            if best_teacher_row.get("leakage_overlap"):
                raise ValueError(f"selected positive teacher leaks: {row['state_example_id']} {best_pair_key}")
        output.append(
            {
                "format": "stage_c1_teacher_condition_v1",
                "state_index": int(row["state_index"]),
                "state_example_id": str(row["state_example_id"]),
                "task_id": str(row["task_id"]),
                "episode_id": str(row["episode_id"]),
                "step_id": int(row["step_id"]),
                "split": str(row["split"]),
                "condition": condition,
                "valid_for_stage_c": not all_missing,
                "all_missing_state": all_missing,
                "no_positive_state": condition == "baseline_teacher",
                "valid_effective_memory_count": valid_count,
                "best_stage_memory_position": best_position,
                "best_memory_id": None if best_memory is None else str(best_memory["memory_id"]),
                "best_memory_index": None if best_memory is None else int(best_memory["memory_index"]),
                "best_pair_key": best_pair_key,
                "best_utility": best_utility,
                "L0": row.get("L0"),
                "Lj_text": None if best_teacher_row is None else best_teacher_row.get("Lj_text"),
                "target_sha256": next((item for item in row["target_sha256_by_memory"] if item), None),
                "memory_text_sha256": None if best_teacher_row is None else best_teacher_row.get("memory_text_sha256"),
            }
        )
    return output


def condition_counts(conditions: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(row["condition"] for row in conditions)
    by_split: dict[str, dict[str, int]] = {}
    for split in sorted({str(row["split"]) for row in conditions}):
        split_rows = [row for row in conditions if str(row["split"]) == split]
        by_split[split] = dict(Counter(row["condition"] for row in split_rows))
        by_split[split]["states"] = len(split_rows)
        by_split[split]["valid_for_stage_c"] = sum(1 for row in split_rows if row["valid_for_stage_c"])
    return {
        "states": len(conditions),
        "positive_teacher": int(counts.get("positive_teacher", 0)),
        "baseline_teacher": int(counts.get("baseline_teacher", 0)),
        "all_missing": int(counts.get("all_missing", 0)),
        "by_split": by_split,
    }


def sparse_bucket_kl(
    student_log_probs: Tensor,
    student_other_log_prob: Tensor,
    teacher_log_probs: Tensor,
    teacher_other_prob: Tensor,
    eps: float = 1.0e-12,
) -> Tensor:
    teacher_probs = teacher_log_probs.exp()
    union_kl = teacher_probs * (teacher_log_probs - student_log_probs)
    other_prob = teacher_other_prob.clamp_min(eps)
    other_kl = other_prob * (other_prob.log() - student_other_log_prob)
    return union_kl.sum(dim=-1) + other_kl


@dataclass(frozen=True)
class StageC1LossWeights:
    teacher_kl: float = 1.0
    action_ce: float = 0.2
    no_positive_preservation: float = 1.0
    delta_l2: float = 1.0e-4
    z_l2: float = 1.0e-4


class ProgramHead(nn.Module):
    def __init__(
        self,
        memory_dim: int,
        program_dim: int = 128,
        hidden_dim: int = 256,
        dropout: float = 0.05,
    ) -> None:
        super().__init__()
        self.memory_dim = int(memory_dim)
        self.program_dim = int(program_dim)
        self.net = nn.Sequential(
            nn.Linear(memory_dim, hidden_dim),
            nn.GELU(),
            nn.LayerNorm(hidden_dim),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, program_dim),
        )

    def forward(self, memory_representations: Tensor) -> Tensor:
        return rms_normalize(torch.tanh(self.net(memory_representations.to(torch.float32))))


class FreeIDProgramHead(nn.Module):
    def __init__(
        self,
        memory_count: int,
        program_dim: int,
        *,
        target_parameter_count: int,
    ) -> None:
        super().__init__()
        self.memory_count = int(memory_count)
        self.program_dim = int(program_dim)
        denom = max(1, self.memory_count + self.program_dim + 1)
        latent_dim = max(program_dim, int(round(target_parameter_count / denom)))
        self.latent_dim = latent_dim
        self.embedding = nn.Parameter(torch.empty(memory_count, latent_dim))
        self.projection = nn.Linear(latent_dim, program_dim)
        nn.init.normal_(self.embedding, mean=0.0, std=0.02)

    def forward(self, memory_representations: Tensor) -> Tensor:
        del memory_representations
        return rms_normalize(torch.tanh(self.projection(self.embedding)))


def parameter_count(module: nn.Module) -> int:
    return sum(param.numel() for param in module.parameters())


def augmented_queries_keys(
    q: Tensor,
    k: Tensor,
    mu: Tensor,
    temperature: Tensor | float,
    *,
    rank: int,
) -> tuple[Tensor, Tensor]:
    temp = torch.as_tensor(temperature, device=k.device, dtype=torch.float32)
    q_bar = torch.cat([q.to(torch.float32), torch.ones(q.shape[0], 1, device=q.device)], dim=1)
    k_scaled = temp * k.to(torch.float32) / math.sqrt(rank)
    k_bar = torch.cat([k_scaled, mu.to(device=k.device, dtype=torch.float32).view(-1, 1)], dim=1)
    return q_bar, k_bar


def signed_scores_from_augmented(q_bar: Tensor, k_bar: Tensor) -> Tensor:
    return q_bar.to(torch.float32) @ k_bar.to(torch.float32).T


class StageC1ProgramField(nn.Module):
    def __init__(
        self,
        *,
        memory_dim: int,
        rank: int = 128,
        program_dim: int = 128,
        hidden_dim: int = 256,
        dropout: float = 0.05,
        eps: float = 1.0e-6,
        program_kind: str = "content",
        memory_count: int | None = None,
        matched_parameter_count: int | None = None,
    ) -> None:
        super().__init__()
        self.rank = int(rank)
        self.augmented_rank = int(rank) + 1
        self.program_dim = int(program_dim)
        self.eps = float(eps)
        self.program_kind = program_kind
        if program_kind == "content":
            self.program_head = ProgramHead(memory_dim, program_dim=program_dim, hidden_dim=hidden_dim, dropout=dropout)
        elif program_kind == "free_id":
            if memory_count is None or matched_parameter_count is None:
                raise ValueError("free_id programs require memory_count and matched_parameter_count")
            self.program_head = FreeIDProgramHead(
                memory_count,
                program_dim,
                target_parameter_count=matched_parameter_count,
            )
        else:
            raise ValueError(f"unknown program_kind: {program_kind}")

    def programs(self, memory_representations: Tensor) -> Tensor:
        return self.program_head(memory_representations)

    def compile_field(
        self,
        k_bar: Tensor,
        programs: Tensor,
        include_mask: Tensor | None = None,
    ) -> tuple[Tensor, Tensor]:
        dtype = k_bar.dtype if k_bar.is_floating_point() else torch.float32
        keys = k_bar.to(dtype=dtype)
        progs = programs.to(device=keys.device, dtype=dtype)
        if include_mask is None:
            return keys.T @ progs, keys.T @ keys
        mask = include_mask.to(device=keys.device, dtype=torch.float32).view(-1, 1)
        masked_keys = keys * mask
        return masked_keys.T @ progs, masked_keys.T @ keys

    def read(
        self,
        q_bar: Tensor,
        k_bar: Tensor,
        programs: Tensor,
        gate: Tensor,
        include_mask: Tensor | None = None,
    ) -> tuple[Tensor, dict[str, Tensor]]:
        dtype = q_bar.dtype if q_bar.is_floating_point() else torch.float32
        qv = q_bar.to(dtype=dtype)
        kv = k_bar.to(device=qv.device, dtype=dtype)
        scores = qv @ kv.T
        if include_mask is not None:
            scores = scores * include_mask.to(device=scores.device, dtype=scores.dtype)
        numerator = scores @ programs.to(device=scores.device, dtype=scores.dtype)
        denom = torch.sqrt(scores.pow(2).sum(dim=1, keepdim=True) + self.eps)
        z = gate.to(device=scores.device, dtype=scores.dtype).view(-1, 1) * numerator / denom
        return z, {"scores": scores, "denominator": denom.squeeze(-1)}


class FixedProgramField(nn.Module):
    def __init__(self, programs: Tensor, *, eps: float = 1.0e-6) -> None:
        super().__init__()
        self.register_buffer("fixed_programs", programs.to(torch.float32))
        self.eps = eps
        self.program_dim = int(programs.shape[1])

    def programs(self, memory_representations: Tensor) -> Tensor:
        del memory_representations
        return self.fixed_programs

    def read(
        self,
        q_bar: Tensor,
        k_bar: Tensor,
        programs: Tensor,
        gate: Tensor,
        include_mask: Tensor | None = None,
    ) -> tuple[Tensor, dict[str, Tensor]]:
        scores = q_bar.to(torch.float32) @ k_bar.to(torch.float32).T
        if include_mask is not None:
            scores = scores * include_mask.to(device=scores.device, dtype=scores.dtype)
        numerator = scores @ programs.to(torch.float32)
        denom = torch.sqrt(scores.pow(2).sum(dim=1, keepdim=True) + self.eps)
        z = gate.to(device=scores.device, dtype=torch.float32).view(-1, 1) * numerator / denom
        return z, {"scores": scores, "denominator": denom.squeeze(-1)}


def explicit_field_read(
    q_bar: Tensor,
    k_bar: Tensor,
    programs: Tensor,
    gate: Tensor,
    include_mask: Tensor | None = None,
    eps: float = 1.0e-6,
) -> Tensor:
    dtype = q_bar.dtype if q_bar.is_floating_point() else torch.float32
    scores = q_bar.to(dtype=dtype) @ k_bar.to(device=q_bar.device, dtype=dtype).T
    if include_mask is not None:
        scores = scores * include_mask.to(scores.dtype)
    numerator = scores @ programs.to(device=scores.device, dtype=scores.dtype)
    denom = torch.sqrt(scores.pow(2).sum(dim=1, keepdim=True) + eps)
    return gate.to(device=scores.device, dtype=scores.dtype).view(-1, 1) * numerator / denom


def validate_program_field_algebra(
    *,
    rank: int = 8,
    program_dim: int = 5,
    count: int = 7,
    seed: int = 20260806,
    eps: float = 1.0e-6,
) -> dict[str, Any]:
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    dtype = torch.float64
    q = torch.randn(3, rank + 1, generator=generator, dtype=dtype)
    q[:, -1] = 1.0
    k = torch.randn(count, rank + 1, generator=generator, dtype=dtype)
    programs = torch.randn(count, program_dim, generator=generator, dtype=dtype)
    gate = torch.sigmoid(torch.randn(3, generator=generator, dtype=dtype))
    field = StageC1ProgramField(memory_dim=rank, rank=rank, program_dim=program_dim, eps=eps).to(dtype=dtype)
    field.program_head = nn.Identity()
    with torch.no_grad():
        z_full, _ = field.read(q, k, programs, gate)
        z_explicit = explicit_field_read(q, k, programs, gate, eps=eps)
        mask = torch.ones(count, dtype=dtype)
        mask[2] = 0.0
        z_leave_one, _ = field.read(q, k, programs, gate, mask)
        z_leave_explicit = explicit_field_read(q, k, programs, gate, mask, eps=eps)
        V, G = field.compile_field(k, programs)
        z_vg = gate.view(-1, 1) * (q @ V) / torch.sqrt(torch.einsum("br,rs,bs->b", q, G, q).view(-1, 1) + eps)
        V_one, G_one = field.compile_field(k[:1], programs[:1])
        V_zero = V_one - torch.outer(k[0], programs[0])
        G_zero = G_one - torch.outer(k[0], k[0])
        replace_v = V_one - torch.outer(k[0], programs[0]) + torch.outer(k[1], programs[1])
        replace_g = G_one - torch.outer(k[0], k[0]) + torch.outer(k[1], k[1])
        order = list(range(count))
        random.Random(seed).shuffle(order)
        V_order = torch.zeros_like(V)
        G_order = torch.zeros_like(G)
        for index in order:
            V_order += torch.outer(k[index], programs[index])
            G_order += torch.outer(k[index], k[index])
        random.Random(seed + 1).shuffle(order)
        for index in order:
            V_order -= torch.outer(k[index], programs[index])
            G_order -= torch.outer(k[index], k[index])
    atol = 1.0e-9
    reversibility_atol = 1.0e-10
    return {
        "format": "stage_c1_program_field_algebra_validation_v1",
        "rank": rank,
        "augmented_rank": rank + 1,
        "program_dim": program_dim,
        "full_read_max_abs_error": float((z_full - z_explicit).abs().max().item()),
        "leave_one_out_max_abs_error": float((z_leave_one - z_leave_explicit).abs().max().item()),
        "vg_read_max_abs_error": float((z_full - z_vg).abs().max().item()),
        "add_remove_v_norm": float(V_zero.norm().item()),
        "add_remove_g_norm": float(G_zero.norm().item()),
        "replace_v_max_abs_error": float((replace_v - torch.outer(k[1], programs[1])).abs().max().item()),
        "replace_g_max_abs_error": float((replace_g - torch.outer(k[1], k[1])).abs().max().item()),
        "arbitrary_order_v_norm": float(V_order.norm().item()),
        "arbitrary_order_g_norm": float(G_order.norm().item()),
        "passed": bool(
            torch.allclose(z_full, z_explicit, atol=atol)
            and torch.allclose(z_leave_one, z_leave_explicit, atol=atol)
            and torch.allclose(z_full, z_vg, atol=atol)
            and torch.allclose(V_zero, torch.zeros_like(V_zero), atol=reversibility_atol)
            and torch.allclose(G_zero, torch.zeros_like(G_zero), atol=reversibility_atol)
            and torch.allclose(replace_v, torch.outer(k[1], programs[1]), atol=reversibility_atol)
            and torch.allclose(replace_g, torch.outer(k[1], k[1]), atol=reversibility_atol)
            and torch.allclose(V_order, torch.zeros_like(V_order), atol=reversibility_atol)
            and torch.allclose(G_order, torch.zeros_like(G_order), atol=reversibility_atol)
        ),
    }


def build_include_mask(rows: Sequence[dict[str, Any]], *, validation_full_bank: bool = True) -> Tensor:
    masks = []
    for row in rows:
        if validation_full_bank and str(row.get("split")) == "validation":
            masks.append([True] * len(row["ordered_effective_memory_ids"]))
        else:
            masks.append([bool(value) for value in row["legal_effective_mask"]])
    return torch.tensor(masks, dtype=torch.bool)


def prepare_selector_payload(
    *,
    selector: SignedResidualField,
    state_representations: Tensor,
    memory_representations: Tensor,
    mu: Tensor,
    device: torch.device,
) -> dict[str, Tensor]:
    selector = selector.to(device).eval()
    for param in selector.parameters():
        param.requires_grad_(False)
    with torch.no_grad():
        state = state_representations.to(device=device, dtype=torch.float32)
        memory = memory_representations.to(device=device, dtype=torch.float32)
        q = selector.encode_state(state)
        k = selector.encode_memory(memory)
        gate = torch.sigmoid(selector.gate_head(state).squeeze(-1))
        temperature = selector.positive_temperature().detach()
        q_bar, k_bar = augmented_queries_keys(q, k, mu.to(device), temperature, rank=selector.rank)
        residual = temperature * (q @ k.T) / math.sqrt(selector.rank)
        scores = mu.to(device).view(1, -1) + residual
    return {
        "q": q.detach(),
        "k": k.detach(),
        "q_bar": q_bar.detach(),
        "k_bar": k_bar.detach(),
        "gate": gate.detach(),
        "scores": scores.detach(),
        "temperature": temperature.detach().view(()),
    }


def validate_selector_preserved(before: dict[str, Tensor], after: dict[str, Tensor], atol: float = 0.0) -> dict[str, Any]:
    errors: dict[str, float] = {}
    for key in ("q", "k", "q_bar", "k_bar", "gate", "scores", "temperature"):
        a = before[key].detach().cpu()
        b = after[key].detach().cpu()
        errors[key] = float((a - b).abs().max().item())
    return {
        "format": "stage_c1_selector_preservation_v1",
        "max_abs_errors": errors,
        "passed": all(value <= atol for value in errors.values()),
        "atol": atol,
    }


def sparse_teacher_kl_from_logits(
    logits: Tensor,
    response_rows: Sequence[dict[str, Any]],
    *,
    target_lengths: Sequence[int],
    target: str = "teacher",
) -> tuple[Tensor, dict[str, Any]]:
    losses: list[Tensor] = []
    cursor = 0
    for row, target_len in zip(response_rows, target_lengths):
        positions = row["target_positions"]
        if int(target_len) != len(positions):
            raise ValueError("target length does not match response-cache positions")
        for pos, item in enumerate(positions):
            row_logits = logits[cursor + pos].to(torch.float32)
            union_ids = torch.tensor(item["union_token_ids"], dtype=torch.long, device=row_logits.device)
            logsumexp = torch.logsumexp(row_logits, dim=-1)
            student_log_probs = row_logits[union_ids] - logsumexp
            union_prob = student_log_probs.exp().sum().clamp(max=1.0 - 1.0e-12)
            student_other_log_prob = torch.log1p(-union_prob)
            teacher_log_probs = torch.tensor(
                item[f"{target}_union_logprobs"],
                dtype=torch.float32,
                device=row_logits.device,
            )
            teacher_other_prob = torch.tensor(
                float(item[f"{target}_other_probability"]),
                dtype=torch.float32,
                device=row_logits.device,
            )
            losses.append(
                sparse_bucket_kl(
                    student_log_probs,
                    student_other_log_prob,
                    teacher_log_probs,
                    teacher_other_prob,
                )
            )
        cursor += int(target_len)
    if cursor != logits.shape[0]:
        raise ValueError(f"target logits row count mismatch: cursor={cursor} logits={logits.shape[0]}")
    if not losses:
        return logits.sum() * 0.0, {"positions": 0}
    values = torch.stack(losses)
    return values.mean(), {"positions": int(values.numel()), "mean": float(values.detach().mean().cpu())}


def target_nll_by_state_from_logits(
    logits: Tensor,
    labels: Tensor,
    *,
    target_lengths: Sequence[int],
) -> list[float]:
    target_mask = labels[..., 1:].ne(-100)
    target_labels = labels[..., 1:][target_mask]
    if target_labels.numel() != logits.shape[0]:
        raise ValueError("logit/label target count mismatch")
    log_probs = F.log_softmax(logits.to(torch.float32), dim=-1)
    nll = -log_probs[torch.arange(logits.shape[0], device=logits.device), target_labels.to(logits.device)]
    out = []
    cursor = 0
    for length in target_lengths:
        out.append(float(nll[cursor : cursor + int(length)].detach().mean().cpu()))
        cursor += int(length)
    return out


def response_cache_state_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    improvements = []
    teacher_improvements = []
    by_condition: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if not row.get("valid_for_stage_c", False):
            continue
        l0 = float(row["L0"])
        teacher_nll = float(row["teacher_mean_target_nll"])
        improvement = l0 - teacher_nll
        teacher_improvements.append(improvement)
        by_condition[str(row["teacher_condition"])].append(improvement)
    return {
        "count": len(rows),
        "valid_for_stage_c": sum(1 for row in rows if row.get("valid_for_stage_c", False)),
        "teacher_improvement": distribution(teacher_improvements),
        "teacher_improvement_by_condition": {key: distribution(values) for key, values in by_condition.items()},
        "improved_fraction": (
            sum(1 for value in teacher_improvements if value > 0.0) / len(teacher_improvements)
            if teacher_improvements
            else None
        ),
        "positive_teacher_count": sum(1 for row in rows if row.get("teacher_condition") == "positive_teacher"),
        "baseline_teacher_count": sum(1 for row in rows if row.get("teacher_condition") == "baseline_teacher"),
        "all_missing_count": sum(1 for row in rows if row.get("teacher_condition") == "all_missing"),
        "mean_selected_utility": (
            sum(float(row["teacher_utility"]) for row in rows if row.get("teacher_utility") is not None)
            / max(1, sum(1 for row in rows if row.get("teacher_utility") is not None))
        ),
        "unused_placeholder": improvements,
    }


def summarize_state_nll_rows(rows: Sequence[dict[str, Any]], *, baseline_key: str = "L0") -> dict[str, Any]:
    if not rows:
        return {"count": 0}
    values = [float(row["student_target_nll"]) for row in rows]
    baseline = [float(row[baseline_key]) for row in rows]
    teacher_kl = [float(row["sparse_teacher_kl"]) for row in rows]
    improvements = [b - v for b, v in zip(baseline, values)]
    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_condition[str(row.get("teacher_condition"))].append(row)
    return {
        "states": len(rows),
        "target_nll": distribution(values),
        "sparse_teacher_kl": distribution(teacher_kl),
        "L0_minus_student": distribution(improvements),
        "improved_fraction": sum(1 for value in improvements if value > 0.0) / len(improvements),
        "worsened_gt_0.01_fraction": sum(1 for value in improvements if value < -0.01) / len(improvements),
        "worsened_gt_0.10_fraction": sum(1 for value in improvements if value < -0.10) / len(improvements),
        "worsened_gt_0.50_fraction": sum(1 for value in improvements if value < -0.50) / len(improvements),
        "by_condition": {
            key: summarize_state_nll_rows(value, baseline_key=baseline_key)
            for key, value in by_condition.items()
            if len(value) != len(rows)
        },
    }


def program_geometry(programs: Tensor) -> dict[str, Any]:
    p = programs.detach().to(torch.float32).cpu()
    return {
        "norm": distribution(p.norm(dim=1).tolist()),
        "pairwise_cosine": pairwise_cosine_summary(p),
        "centered_spectrum": singular_summary(p - p.mean(dim=0, keepdim=True)),
        "coordinate_variance": distribution(p.var(dim=0, unbiased=False).tolist()),
    }


def z_geometry(z: Tensor, *, reference: Tensor | None = None) -> dict[str, Any]:
    zc = z.detach().to(torch.float32).cpu()
    output = {
        "norm": distribution(zc.norm(dim=1).tolist()),
        "pairwise_cosine": pairwise_cosine_summary(zc),
        "centered_spectrum": singular_summary(zc - zc.mean(dim=0, keepdim=True)),
        "coordinate_variance": distribution(zc.var(dim=0, unbiased=False).tolist()),
    }
    if reference is not None:
        ref = reference.detach().to(torch.float32).cpu()
        output["mean_abs_delta_vs_reference"] = float((zc - ref).abs().mean().item())
        output["mean_norm_delta_vs_reference"] = float((zc - ref).norm(dim=1).mean().item())
    return output


def paired_ci(
    rows: dict[str, list[dict[str, Any]]],
    *,
    metrics: Sequence[str] = ("student_target_nll", "sparse_teacher_kl"),
    baseline_name: str = "correct",
    seed: int = 13,
) -> dict[str, Any]:
    if baseline_name not in rows:
        return {}
    by_id = {
        name: {str(row["state_example_id"]): row for row in payload}
        for name, payload in rows.items()
    }
    output = {}
    for name, table in by_id.items():
        if name == baseline_name:
            continue
        common = sorted(set(by_id[baseline_name]).intersection(table))
        if not common:
            continue
        generator = random.Random(seed)
        metric_output = {}
        for metric in metrics:
            values = [
                float(by_id[baseline_name][state_id][metric]) - float(table[state_id][metric])
                for state_id in common
            ]
            if len(values) < 2:
                metric_output[metric] = {"count": len(values), "mean": values[0] if values else None}
                continue
            boots = []
            for _ in range(1000):
                sample = [values[generator.randrange(len(values))] for __ in range(len(values))]
                boots.append(sum(sample) / len(sample))
            boots.sort()
            metric_output[metric] = {
                "count": len(values),
                "mean": sum(values) / len(values),
                "lo": boots[int(0.025 * (len(boots) - 1))],
                "hi": boots[int(0.975 * (len(boots) - 1))],
                "definition": f"{baseline_name}_minus_{name}",
            }
        output[name] = metric_output
    return output


def load_signed_selector_checkpoint(
    *,
    checkpoint: dict[str, Any],
    state_dim: int,
    memory_dim: int,
    rank: int = 128,
) -> SignedResidualField:
    if checkpoint.get("model_kind") != "signed_core_field_r128":
        raise ValueError(f"unexpected selector checkpoint model_kind: {checkpoint.get('model_kind')}")
    if checkpoint.get("prior_kind") != "empirical_train_mu":
        raise ValueError(f"unexpected selector prior kind: {checkpoint.get('prior_kind')}")
    model = SignedResidualField(state_dim, memory_dim, rank=rank, hidden_dim=256, dropout=0.05)
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    for param in model.parameters():
        param.requires_grad_(False)
    return model


def selector_state_hash(selector: nn.Module) -> dict[str, Tensor]:
    return {key: value.detach().cpu().clone() for key, value in selector.state_dict().items()}


def selector_parameter_change(before: dict[str, Tensor], selector: nn.Module) -> dict[str, Any]:
    errors = {}
    current = selector.state_dict()
    for key, value in before.items():
        errors[key] = float((value - current[key].detach().cpu()).abs().max().item())
    return {
        "max_abs_by_tensor": errors,
        "max_abs": max(errors.values(), default=0.0),
        "passed": max(errors.values(), default=0.0) == 0.0,
    }


def make_fixed_random_programs(memory_count: int, program_dim: int, *, seed: int) -> Tensor:
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    return rms_normalize(torch.randn(memory_count, program_dim, generator=generator, dtype=torch.float32))


def shuffled_tensor(value: Tensor, *, seed: int, dim: int = 0) -> Tensor:
    if value.shape[dim] <= 1:
        return value
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    order = torch.randperm(value.shape[dim], generator=generator).to(value.device)
    return value.index_select(dim, order)


def summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for split in ("train", "validation"):
        output[split] = {}
        for metric in (
            "target_nll",
            "sparse_teacher_kl",
            "L0_minus_student",
            "improved_fraction",
            "worsened_gt_0.01_fraction",
            "worsened_gt_0.10_fraction",
            "worsened_gt_0.50_fraction",
        ):
            values = []
            for run in runs:
                summary = run.get(split, {}).get("correct", {}).get("summary", {})
                value: Any
                if metric in {"target_nll", "sparse_teacher_kl", "L0_minus_student"}:
                    value = summary.get(metric, {}).get("mean")
                else:
                    value = summary.get(metric)
                if value is not None:
                    values.append(float(value))
            output[split][metric] = mean_std(values)
    for control in (
        "shuffled_state",
        "shuffled_program",
        "fixed_random_program",
        "free_id_program",
        "mean_program",
        "zero_program",
        "global_prior_only",
    ):
        output[f"correct_minus_{control}"] = {
            metric: mean_std(
                run.get("validation", {}).get("control_deltas", {}).get(control, {}).get(metric, {}).get("mean")
                for run in runs
                if run.get("validation", {}).get("control_deltas", {}).get(control, {}).get(metric, {}).get("mean") is not None
            )
            for metric in ("student_target_nll", "sparse_teacher_kl")
        }
    output["program_centered_effective_rank"] = mean_std(
        run.get("program_geometry", {}).get("centered_spectrum", {}).get("effective_rank")
        for run in runs
        if run.get("program_geometry", {}).get("centered_spectrum", {}).get("effective_rank") is not None
    )
    output["z_centered_effective_rank"] = mean_std(
        run.get("validation", {}).get("correct", {}).get("z_geometry", {}).get("centered_spectrum", {}).get("effective_rank")
        for run in runs
        if run.get("validation", {}).get("correct", {}).get("z_geometry", {}).get("centered_spectrum", {}).get("effective_rank") is not None
    )
    return output


def stage_c1_decision(
    *,
    runs: list[dict[str, Any]],
    selector_preservation: list[dict[str, Any]],
    cache_validation_passed: bool,
    tiny_overfit_passed: bool,
    leave_one_out: dict[str, Any],
) -> dict[str, Any]:
    if not cache_validation_passed:
        branch = "teacher_response_cache_invalid"
        passed = False
    elif not tiny_overfit_passed:
        branch = "program_injector_gradient_path_insufficient"
        passed = False
    else:
        summary = summarize_runs(runs)
        val = summary.get("validation", {})
        nll_improvement = val.get("L0_minus_student", {}).get("mean")
        no_pos_degradation = _no_positive_degradation(runs)
        ci_ok = _ci_upper_below_zero(runs, "bare_qwen_zero_field", "student_target_nll")
        kl_shuffled_ok = _ci_upper_below_zero(runs, "shuffled_state", "sparse_teacher_kl") and _ci_upper_below_zero(
            runs,
            "shuffled_program",
            "sparse_teacher_kl",
        )
        random_ok = _mean_delta_over_control(runs, "fixed_random_program", "student_target_nll") < 0.0
        free_id_available = _control_available(runs, "free_id_program", "student_target_nll")
        free_id_ok = free_id_available and _mean_delta_over_control(runs, "free_id_program", "student_target_nll") < 0.0
        control_ok = (
            _mean_delta_over_control(runs, "shuffled_state", "sparse_teacher_kl") < 0.0
            and _mean_delta_over_control(runs, "shuffled_program", "sparse_teacher_kl") < 0.0
            and random_ok
            and free_id_ok
        )
        rank_ok = bool(
            (summary.get("program_centered_effective_rank", {}).get("mean") or 0.0) > 2.0
            and (summary.get("z_centered_effective_rank", {}).get("mean") or 0.0) > 2.0
        )
        selector_ok = all(bool(item.get("passed")) for item in selector_preservation)
        loo_ok = bool(leave_one_out.get("teacher_best_hurts_more_fraction", 0.0) > 0.5)
        no_pos_ok = no_pos_degradation is not None and no_pos_degradation <= 0.02
        passed = bool(
            nll_improvement is not None
            and nll_improvement > 0.0
            and ci_ok
            and kl_shuffled_ok
            and control_ok
            and no_pos_ok
            and loo_ok
            and rank_ok
            and selector_ok
        )
        if passed:
            branch = "stage_c1_passed_signed_program_memory_content_compilation"
        elif not control_ok:
            branch = "signed_program_channel_not_behaviorally_useful_or_content_not_distinct"
        elif not no_pos_ok:
            branch = "no_positive_states_not_preserved"
        elif not loo_ok:
            branch = "leave_one_out_does_not_track_teacher_best_memory"
        elif not rank_ok:
            branch = "program_or_z_geometry_collapsed"
        elif not selector_ok:
            branch = "selector_parameters_or_metrics_changed"
        else:
            branch = "stage_c1_gate_failed_target_or_teacher_kl"
    return {
        "format": "stage_c1_decision_gate_v1",
        "passed": passed,
        "branch": branch,
        "stage_c2_allowed": False,
        "values": {
            "cache_validation_passed": cache_validation_passed,
            "tiny_overfit_passed": tiny_overfit_passed,
            "mean_no_positive_degradation": _no_positive_degradation(runs) if runs else None,
        },
    }


def _mean_delta_over_control(runs: list[dict[str, Any]], control: str, metric: str) -> float:
    values = []
    for run in runs:
        item = run.get("validation", {}).get("control_deltas", {}).get(control, {}).get(metric, {}).get("mean")
        if item is not None:
            values.append(float(item))
    return sum(values) / len(values) if values else 0.0


def _control_available(runs: list[dict[str, Any]], control: str, metric: str) -> bool:
    return any(
        run.get("validation", {}).get("control_deltas", {}).get(control, {}).get(metric, {}).get("mean") is not None
        for run in runs
    )


def _ci_upper_below_zero(runs: list[dict[str, Any]], control: str, metric: str) -> bool:
    ok = []
    for run in runs:
        ci = run.get("validation", {}).get("bootstrap_ci", {}).get(control, {}).get(metric)
        if ci and ci.get("hi") is not None:
            ok.append(float(ci["hi"]) < 0.0)
    return bool(ok) and all(ok)


def _no_positive_degradation(runs: list[dict[str, Any]]) -> float | None:
    values = []
    for run in runs:
        correct_rows = run.get("validation", {}).get("correct", {}).get("rows", [])
        for row in correct_rows:
            if row.get("teacher_condition") == "baseline_teacher":
                values.append(float(row["student_target_nll"]) - float(row["L0"]))
    return sum(values) / len(values) if values else None
