from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
import random
from typing import Any, Iterable, Sequence

import torch
from torch import Tensor, nn

from rcmf.training.addressing_4b import distribution, mean_std, pairwise_cosine_summary, singular_summary
from rcmf.training.stage_c1 import FreeIDProgramHead, ProgramHead, rms_normalize
from rcmf.utils.serialization import sha256_text


PAIR_SELECTION_VERSION = "stage_c_pair_selection_5d_v1"
PAIR_RESPONSE_CACHE_VERSION = "stage_c_pair_response_cache_5d_v1"
PAIR_RESPONSE_SCORING_DEFINITION = "single_raw_memory_pair_target_top64_delta_v1"
PAIR_GROUNDING_VERSION = "stage_c_pair_grounding_5d_v1"
POSITIVE_UTILITY_EPS = 0.01
STRONG_POSITIVE = 0.05
STRONG_NEGATIVE = -0.05


@dataclass(frozen=True)
class PairSelectionConfig:
    train_per_category: int = 8
    validation_per_category: int = 4
    neutral_eps: float = POSITIVE_UTILITY_EPS
    seed: int = 20260807

    def quota_for_split(self, split: str) -> int:
        if split == "train":
            return self.train_per_category
        if split == "validation":
            return self.validation_per_category
        raise ValueError(f"unsupported split: {split}")


@dataclass(frozen=True)
class PairGroundingLossWeights:
    delta_huber: float = 1.0
    teacher_kl: float = 0.2
    positive_ce: float = 0.05
    neutral_preservation: float = 0.5
    ratio_penalty: float = 0.2
    huber_delta: float = 0.1
    ratio_target: float = 1.0


def pair_key(example_index: int, memory_index: int) -> str:
    return f"e{example_index}:m{memory_index}"


def pair_id(state_example_id: str, memory_id: str) -> str:
    return f"{state_example_id}::memory::{memory_id}"


def utility_category(utility: float, *, neutral_eps: float = POSITIVE_UTILITY_EPS) -> str:
    if utility > neutral_eps:
        return "positive"
    if utility < -neutral_eps:
        return "negative"
    return "neutral"


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _candidate_rows_for_memory(
    label_rows: Sequence[dict[str, Any]],
    memory_bank: Sequence[dict[str, Any]],
    *,
    memory_stage_index: int,
    split: str,
    neutral_eps: float,
) -> dict[str, list[dict[str, Any]]]:
    memory = memory_bank[memory_stage_index]
    buckets: dict[str, list[dict[str, Any]]] = {"positive": [], "neutral": [], "negative": []}
    for row in label_rows:
        if str(row.get("split")) != split:
            continue
        valid_mask = row.get("valid_mask", [])
        utilities = row.get("raw_utility", [])
        source_pair_keys = row.get("source_pair_keys", [])
        if memory_stage_index >= len(valid_mask) or memory_stage_index >= len(utilities):
            raise ValueError(f"memory index {memory_stage_index} out of range for {row.get('state_example_id')}")
        if not bool(valid_mask[memory_stage_index]):
            continue
        utility = _finite_float(utilities[memory_stage_index])
        if utility is None:
            continue
        source_pair_key = source_pair_keys[memory_stage_index] if memory_stage_index < len(source_pair_keys) else None
        if not source_pair_key:
            raise ValueError(f"valid pair missing source_pair_key: {row.get('state_example_id')} memory={memory_stage_index}")
        category = utility_category(utility, neutral_eps=neutral_eps)
        item = {
            "format": PAIR_SELECTION_VERSION,
            "split": split,
            "state_index": int(row["state_index"]),
            "state_example_id": str(row["state_example_id"]),
            "task_id": str(row["task_id"]),
            "episode_id": str(row["episode_id"]),
            "step_id": int(row["step_id"]),
            "memory_stage_index": int(memory_stage_index),
            "memory_index": int(memory["memory_index"]),
            "memory_id": str(memory["memory_id"]),
            "memory_task_id": str(memory["task_id"]),
            "memory_episode_id": str(memory.get("episode_id")),
            "pair_key": str(source_pair_key),
            "pair_id": pair_id(str(row["state_example_id"]), str(memory["memory_id"])),
            "raw_utility": float(utility),
            "L0": row.get("L0"),
            "utility_category": category,
            "target_sha256": row.get("target_sha256_by_memory", [None])[memory_stage_index],
            "memory_text_sha256": row.get("memory_text_sha256_by_memory", [None])[memory_stage_index],
        }
        buckets[category].append(item)
    buckets["positive"].sort(key=lambda item: (-float(item["raw_utility"]), item["state_example_id"]))
    buckets["neutral"].sort(key=lambda item: (abs(float(item["raw_utility"])), item["state_example_id"]))
    buckets["negative"].sort(key=lambda item: (float(item["raw_utility"]), item["state_example_id"]))
    return buckets


def select_stratified_pair_set(
    label_rows: Sequence[dict[str, Any]],
    memory_bank: Sequence[dict[str, Any]],
    *,
    config: PairSelectionConfig | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = config or PairSelectionConfig()
    selected_by_pair: dict[str, dict[str, Any]] = {}
    coverage_rows: list[dict[str, Any]] = []
    for split in ("train", "validation"):
        quota = config.quota_for_split(split)
        for memory_stage_index in range(len(memory_bank)):
            buckets = _candidate_rows_for_memory(
                label_rows,
                memory_bank,
                memory_stage_index=memory_stage_index,
                split=split,
                neutral_eps=config.neutral_eps,
            )
            already_for_memory: set[str] = set()
            coverage: dict[str, Any] = {
                "split": split,
                "memory_stage_index": memory_stage_index,
                "memory_id": str(memory_bank[memory_stage_index]["memory_id"]),
                "requested_per_category": quota,
            }
            for category in ("positive", "neutral", "negative"):
                chosen = buckets[category][:quota]
                coverage[f"{category}_available"] = len(buckets[category])
                coverage[f"{category}_selected"] = len(chosen)
                coverage[f"{category}_missing"] = max(0, quota - len(chosen))
                for item in chosen:
                    row = dict(item)
                    row["selection_category"] = category
                    row["selection_rank_within_memory_category"] = len(
                        [x for x in selected_by_pair.values() if x["memory_stage_index"] == memory_stage_index and x["split"] == split and x["selection_category"] == category]
                    ) + 1
                    selected_by_pair[row["pair_id"]] = row
                    already_for_memory.add(row["pair_id"])
            remaining = [
                item
                for category_items in buckets.values()
                for item in category_items
                if item["pair_id"] not in already_for_memory
            ]
            rng = random.Random(config.seed + 7919 * memory_stage_index + (0 if split == "train" else 1_000_003))
            rng.shuffle(remaining)
            chosen_random = remaining[:quota]
            coverage["random_available"] = len(remaining)
            coverage["random_selected"] = len(chosen_random)
            coverage["random_missing"] = max(0, quota - len(chosen_random))
            for rank, item in enumerate(chosen_random, start=1):
                row = dict(item)
                row["selection_category"] = "random"
                row["selection_rank_within_memory_category"] = rank
                selected_by_pair[row["pair_id"]] = row
            coverage_rows.append(coverage)
    selected = sorted(
        selected_by_pair.values(),
        key=lambda item: (item["split"], int(item["memory_stage_index"]), item["selection_category"], item["state_example_id"]),
    )
    summary = summarize_pair_selection(selected, coverage_rows, memory_count=len(memory_bank), config=config)
    return selected, summary


def summarize_pair_selection(
    selected: Sequence[dict[str, Any]],
    coverage_rows: Sequence[dict[str, Any]],
    *,
    memory_count: int,
    config: PairSelectionConfig,
) -> dict[str, Any]:
    by_split_category: dict[str, Counter[str]] = defaultdict(Counter)
    utility_by_split_category: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in selected:
        split = str(row["split"])
        category = str(row["selection_category"])
        by_split_category[split][category] += 1
        utility_by_split_category[split][category].append(float(row["raw_utility"]))
    missing_rows = []
    for row in coverage_rows:
        for category in ("positive", "neutral", "negative", "random"):
            missing = int(row.get(f"{category}_missing", 0))
            if missing:
                missing_rows.append(
                    {
                        "split": row["split"],
                        "memory_stage_index": row["memory_stage_index"],
                        "memory_id": row["memory_id"],
                        "category": category,
                        "missing": missing,
                        "available": row.get(f"{category}_available"),
                        "requested": row["requested_per_category"],
                    }
                )
    return {
        "format": "stage_c_pair_selection_summary_5d_v1",
        "selection_version": PAIR_SELECTION_VERSION,
        "memory_count": memory_count,
        "selected_pair_count": len(selected),
        "unique_pair_count": len({str(row["pair_id"]) for row in selected}),
        "config": {
            "train_per_category": config.train_per_category,
            "validation_per_category": config.validation_per_category,
            "neutral_eps": config.neutral_eps,
            "seed": config.seed,
        },
        "by_split_category": {split: dict(counter) for split, counter in by_split_category.items()},
        "utility_by_split_category": {
            split: {category: distribution(values) for category, values in categories.items()}
            for split, categories in utility_by_split_category.items()
        },
        "coverage_rows": list(coverage_rows),
        "missing_category_slots": missing_rows,
        "missing_category_slot_count": len(missing_rows),
    }


def add_teacher_delta_fields(positions: Sequence[dict[str, Any]], *, eps: float = 1.0e-12) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in positions:
        row = dict(item)
        baseline = [float(value) for value in row["baseline_union_logprobs"]]
        teacher = [float(value) for value in row["teacher_union_logprobs"]]
        if len(baseline) != len(teacher):
            raise ValueError("baseline and teacher union lengths differ")
        row["delta_teacher_union_logprobs"] = [t - b for b, t in zip(baseline, teacher)]
        row["baseline_other_logprob"] = math.log(max(float(row["baseline_other_probability"]), eps))
        row["teacher_other_logprob"] = math.log(max(float(row["teacher_other_probability"]), eps))
        row["delta_teacher_other_logprob"] = row["teacher_other_logprob"] - row["baseline_other_logprob"]
        output.append(row)
    return output


def validate_pair_response_cache(
    rows: Sequence[dict[str, Any]],
    *,
    selected_pairs: Sequence[dict[str, Any]],
    teacher_rows: dict[str, dict[str, Any]],
    tolerance: float = 2.0e-4,
    bucket_tolerance: float = 1.0e-5,
) -> dict[str, Any]:
    errors: list[str] = []
    by_pair: dict[str, dict[str, Any]] = {}
    expected = {str(row["pair_id"]): row for row in selected_pairs}
    for row in rows:
        pid = str(row.get("pair_id"))
        if pid in by_pair:
            errors.append(f"duplicate_pair:{pid}")
        by_pair[pid] = row
        if row.get("format") != PAIR_RESPONSE_CACHE_VERSION:
            errors.append(f"{pid}:bad_format:{row.get('format')}")
        if row.get("scoring_definition") != PAIR_RESPONSE_SCORING_DEFINITION:
            errors.append(f"{pid}:bad_scoring_definition:{row.get('scoring_definition')}")
        if row.get("truncated"):
            errors.append(f"{pid}:truncated")
        if int(row.get("teacher_total_tokens_with_target", 0)) > int(row.get("context_limit", 0)):
            errors.append(f"{pid}:teacher_over_context_without_mask")
        selected = expected.get(pid)
        if selected is None:
            errors.append(f"unexpected_pair:{pid}")
        else:
            for key in ("state_example_id", "memory_id", "pair_key", "split"):
                if str(row.get(key)) != str(selected.get(key)):
                    errors.append(f"{pid}:{key}_mismatch:{row.get(key)}:{selected.get(key)}")
            if row.get("target_sha256") and selected.get("target_sha256") and str(row["target_sha256"]) != str(selected["target_sha256"]):
                errors.append(f"{pid}:target_hash_mismatch")
            if row.get("memory_text_sha256") and selected.get("memory_text_sha256") and str(row["memory_text_sha256"]) != str(selected["memory_text_sha256"]):
                errors.append(f"{pid}:memory_hash_mismatch")
        teacher = teacher_rows.get(str(row.get("pair_key")))
        if teacher is None:
            errors.append(f"{pid}:missing_teacher_row")
        else:
            if teacher.get("leakage_overlap"):
                errors.append(f"{pid}:leakage:{teacher.get('leakage_overlap')}")
            if str(teacher.get("candidate_memory_id")) != str(row.get("memory_id")):
                errors.append(f"{pid}:teacher_memory_id_mismatch")
            if not bool(teacher.get("valid_for_loss")):
                errors.append(f"{pid}:teacher_row_not_valid_for_loss")
            if str(teacher.get("memory_text_sha256")) != str(row.get("memory_text_sha256")):
                errors.append(f"{pid}:teacher_memory_hash_mismatch")
        positions = row.get("target_positions", [])
        if len(positions) != int(row.get("target_tokens", -1)):
            errors.append(f"{pid}:position_count_mismatch")
        if positions:
            baseline_nll = -sum(float(item["baseline_target_logprob"]) for item in positions) / len(positions)
            teacher_nll = -sum(float(item["teacher_target_logprob"]) for item in positions) / len(positions)
            if abs(baseline_nll - float(row["baseline_mean_target_nll"])) > tolerance:
                errors.append(f"{pid}:baseline_position_nll_mismatch:{baseline_nll}:{row['baseline_mean_target_nll']}")
            if abs(teacher_nll - float(row["teacher_mean_target_nll"])) > tolerance:
                errors.append(f"{pid}:teacher_position_nll_mismatch:{teacher_nll}:{row['teacher_mean_target_nll']}")
            if row.get("L0") is not None and abs(baseline_nll - float(row["L0"])) > tolerance:
                errors.append(f"{pid}:L0_reproduction_mismatch:{baseline_nll}:{row.get('L0')}")
            if row.get("Lj_text") is not None and abs(teacher_nll - float(row["Lj_text"])) > tolerance:
                errors.append(f"{pid}:Lj_reproduction_mismatch:{teacher_nll}:{row.get('Lj_text')}")
            utility = baseline_nll - teacher_nll
            if row.get("text_utility") is not None and abs(utility - float(row["text_utility"])) > tolerance:
                errors.append(f"{pid}:utility_reproduction_mismatch:{utility}:{row.get('text_utility')}")
        for pos, item in enumerate(positions):
            if len(item.get("delta_teacher_union_logprobs", [])) != len(item.get("union_token_ids", [])):
                errors.append(f"{pid}:delta_teacher_length_mismatch:{pos}")
            for prefix in ("baseline", "teacher"):
                probs = torch.tensor(item[f"{prefix}_union_logprobs"], dtype=torch.float64).exp()
                total = float(probs.sum().item()) + float(item[f"{prefix}_other_probability"])
                if abs(total - 1.0) > bucket_tolerance:
                    errors.append(f"{pid}:probability_bucket_sum:{prefix}:{pos}:{total}")
            for base, teacher_lp, delta in zip(
                item.get("baseline_union_logprobs", []),
                item.get("teacher_union_logprobs", []),
                item.get("delta_teacher_union_logprobs", []),
            ):
                if abs((float(teacher_lp) - float(base)) - float(delta)) > 1.0e-8:
                    errors.append(f"{pid}:delta_teacher_value_mismatch:{pos}")
                    break
    missing = sorted(set(expected).difference(by_pair))
    unexpected = sorted(set(by_pair).difference(expected))
    if missing:
        errors.append(f"missing_pairs:{missing[:10]}")
    if unexpected:
        errors.append(f"unexpected_pairs:{unexpected[:10]}")
    return {
        "format": "stage_c_pair_response_cache_validation_5d_v1",
        "passed": not errors,
        "error_count": len(errors),
        "errors_first_50": errors[:50],
        "pair_count": len(rows),
        "expected_pair_count": len(expected),
        "target_nll_tolerance": tolerance,
        "probability_bucket_tolerance": bucket_tolerance,
    }


class SingleMemoryProgramModel(nn.Module):
    def __init__(
        self,
        *,
        memory_dim: int,
        memory_count: int,
        program_dim: int = 128,
        hidden_dim: int = 256,
        dropout: float = 0.05,
        program_kind: str = "content",
        matched_parameter_count: int | None = None,
        fixed_programs: Tensor | None = None,
    ) -> None:
        super().__init__()
        self.memory_dim = int(memory_dim)
        self.memory_count = int(memory_count)
        self.program_dim = int(program_dim)
        self.program_kind = program_kind
        if program_kind == "content":
            self.program_head: nn.Module = ProgramHead(memory_dim, program_dim=program_dim, hidden_dim=hidden_dim, dropout=dropout)
        elif program_kind == "free_id":
            if matched_parameter_count is None:
                raise ValueError("free_id program requires matched_parameter_count")
            self.program_head = FreeIDProgramHead(memory_count, program_dim, target_parameter_count=int(matched_parameter_count))
        elif program_kind == "fixed_random":
            if fixed_programs is None:
                raise ValueError("fixed_random program requires fixed_programs")
            self.register_buffer("fixed_programs", rms_normalize(fixed_programs.to(torch.float32)))
            self.program_head = nn.Identity()
        else:
            raise ValueError(f"unknown program_kind: {program_kind}")

    def programs(self, memory_representations: Tensor) -> Tensor:
        if self.program_kind == "fixed_random":
            return self.fixed_programs.to(device=memory_representations.device, dtype=torch.float32)
        return self.program_head(memory_representations.to(torch.float32))

    def z_for_memory_indices(self, memory_representations: Tensor, memory_stage_indices: Tensor) -> Tensor:
        programs = self.programs(memory_representations)
        return programs.index_select(0, memory_stage_indices.to(device=programs.device, dtype=torch.long))


def make_fixed_random_programs(memory_count: int, program_dim: int, *, seed: int) -> Tensor:
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    return rms_normalize(torch.randn(memory_count, program_dim, generator=generator, dtype=torch.float32))


def parameter_count(module: nn.Module) -> int:
    return sum(param.numel() for param in module.parameters())


def paired_bootstrap_ci(
    rows_by_name: dict[str, Sequence[dict[str, Any]]],
    *,
    baseline_name: str,
    metrics: Sequence[str],
    key_field: str = "pair_id",
    seed: int = 13,
    samples: int = 1000,
) -> dict[str, Any]:
    if baseline_name not in rows_by_name:
        return {}
    by_key = {
        name: {str(row[key_field]): row for row in rows}
        for name, rows in rows_by_name.items()
    }
    output: dict[str, Any] = {}
    for name, table in by_key.items():
        if name == baseline_name:
            continue
        common = sorted(set(by_key[baseline_name]).intersection(table))
        metric_output: dict[str, Any] = {}
        rng = random.Random(seed)
        for metric in metrics:
            values = [
                float(by_key[baseline_name][key][metric]) - float(table[key][metric])
                for key in common
                if metric in by_key[baseline_name][key] and metric in table[key]
            ]
            if not values:
                metric_output[metric] = {"count": 0}
                continue
            if len(values) == 1:
                metric_output[metric] = {
                    "count": 1,
                    "mean": values[0],
                    "lo": values[0],
                    "hi": values[0],
                    "definition": f"{baseline_name}_minus_{name}",
                }
                continue
            boots = []
            for _ in range(samples):
                sample = [values[rng.randrange(len(values))] for __ in range(len(values))]
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


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    pairs = [(float(x), float(y)) for x, y in zip(xs, ys) if math.isfinite(float(x)) and math.isfinite(float(y))]
    if len(pairs) < 2:
        return None
    x_values = [x for x, _ in pairs]
    y_values = [y for _, y in pairs]
    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(y_values) / len(y_values)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    x_denom = math.sqrt(sum((x - x_mean) ** 2 for x in x_values))
    y_denom = math.sqrt(sum((y - y_mean) ** 2 for y in y_values))
    if x_denom == 0.0 or y_denom == 0.0:
        return None
    return numerator / (x_denom * y_denom)


def _rankdata(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        rank = (cursor + 1 + end) / 2.0
        for index in order[cursor:end]:
            ranks[index] = rank
        cursor = end
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    pairs = [(float(x), float(y)) for x, y in zip(xs, ys) if math.isfinite(float(x)) and math.isfinite(float(y))]
    if len(pairs) < 2:
        return None
    x_values = [x for x, _ in pairs]
    y_values = [y for _, y in pairs]
    return _pearson(_rankdata(x_values), _rankdata(y_values))


def summarize_pair_eval_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"pairs": 0}
    u_text = [float(row["u_text"]) for row in rows]
    u_program = [float(row["u_program"]) for row in rows]
    delta_huber = [float(row["behavioral_delta_huber"]) for row in rows]
    delta_mse = [float(row["behavioral_delta_mse"]) for row in rows]
    kl = [float(row["sparse_teacher_kl"]) for row in rows]
    nll = [float(row["student_target_nll"]) for row in rows]
    target_delta_error = [float(row["target_token_delta_error"]) for row in rows]
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_category[str(row["utility_category"])].append(row)
    sign_agreement_rows = [
        row
        for row in rows
        if str(row["utility_category"]) in {"positive", "negative"}
    ]
    sign_agreement = None
    if sign_agreement_rows:
        sign_agreement = sum(
            1
            for row in sign_agreement_rows
            if (float(row["u_text"]) > 0 and float(row["u_program"]) > 0)
            or (float(row["u_text"]) < 0 and float(row["u_program"]) < 0)
        ) / len(sign_agreement_rows)
    return {
        "pairs": len(rows),
        "u_text": distribution(u_text),
        "u_program": distribution(u_program),
        "u_text_vs_u_program_pearson": _pearson(u_text, u_program),
        "u_text_vs_u_program_spearman": spearman(u_text, u_program),
        "positive_negative_sign_agreement": sign_agreement,
        "target_nll": distribution(nll),
        "sparse_teacher_kl": distribution(kl),
        "behavioral_delta_huber": distribution(delta_huber),
        "behavioral_delta_mse": distribution(delta_mse),
        "target_token_delta_error": distribution(target_delta_error),
        "improved_fraction": sum(1 for row in rows if float(row["u_program"]) > 0.0) / len(rows),
        "worsened_fraction": sum(1 for row in rows if float(row["u_program"]) < 0.0) / len(rows),
        "category_counts": dict(Counter(str(row["utility_category"]) for row in rows)),
        "by_category": {
            category: {
                "pairs": len(category_rows),
                "u_program": distribution(float(row["u_program"]) for row in category_rows),
                "target_nll": distribution(float(row["student_target_nll"]) for row in category_rows),
                "behavioral_delta_huber": distribution(float(row["behavioral_delta_huber"]) for row in category_rows),
            }
            for category, category_rows in by_category.items()
        },
    }


def program_geometry(programs: Tensor) -> dict[str, Any]:
    p = programs.detach().to(torch.float32).cpu()
    if p.numel() == 0:
        return {"count": 0}
    finite = torch.isfinite(p).all(dim=1)
    nonfinite_rows = int((~finite).sum().item())
    if nonfinite_rows:
        p = p[finite]
    return {
        "count": int(p.shape[0]),
        "nonfinite_rows": nonfinite_rows,
        "norm": distribution(p.norm(dim=1).tolist()),
        "pairwise_cosine": pairwise_cosine_summary(p),
        "centered_spectrum": singular_summary(p - p.mean(dim=0, keepdim=True)),
        "coordinate_variance": distribution(p.var(dim=0, unbiased=False).tolist()),
    }


def representation_program_similarity(memory_representations: Tensor, programs: Tensor) -> dict[str, Any]:
    mem = memory_representations.detach().to(torch.float32).cpu()
    prog = programs.detach().to(torch.float32).cpu()
    if mem.shape[0] != prog.shape[0] or mem.shape[0] < 2:
        return {"pair_count": 0}
    mem_cos = torch.nn.functional.normalize(mem, dim=-1) @ torch.nn.functional.normalize(mem, dim=-1).T
    prog_cos = torch.nn.functional.normalize(prog, dim=-1) @ torch.nn.functional.normalize(prog, dim=-1).T
    mask = ~torch.eye(mem_cos.shape[0], dtype=torch.bool)
    mem_values = mem_cos[mask].tolist()
    prog_values = prog_cos[mask].tolist()
    return {
        "pair_count": len(mem_values),
        "memory_similarity": distribution(mem_values),
        "program_similarity": distribution(prog_values),
        "pearson": _pearson(mem_values, prog_values),
        "spearman": spearman(mem_values, prog_values),
    }


def deterministic_memory_folds(memory_count: int, *, folds: int = 5, seed: int = 41) -> list[dict[str, Any]]:
    indices = list(range(memory_count))
    rng = random.Random(seed)
    rng.shuffle(indices)
    output = []
    for fold_index in range(folds):
        heldout = sorted(indices[fold_index::folds])
        train = sorted(index for index in indices if index not in set(heldout))
        output.append({"fold": fold_index, "train_memory_stage_indices": train, "heldout_memory_stage_indices": heldout})
    return output


def category_coverage(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    by_split = defaultdict(Counter)
    by_memory = defaultdict(Counter)
    for row in rows:
        by_split[str(row["split"])][str(row["selection_category"])] += 1
        by_memory[(str(row["split"]), str(row["memory_id"]))][str(row["selection_category"])] += 1
    return {
        "rows": len(rows),
        "by_split": {key: dict(value) for key, value in by_split.items()},
        "by_split_memory": [
            {"split": split, "memory_id": memory_id, **dict(counter)}
            for (split, memory_id), counter in sorted(by_memory.items())
        ],
    }


def hash_pair_cache_sources(row: dict[str, Any]) -> str:
    text = "|".join(
        str(row.get(key))
        for key in (
            "state_example_id",
            "memory_id",
            "pair_key",
            "target_token_sha256",
            "memory_text_sha256",
            "prompt_sha256",
            "teacher_prompt_sha256",
        )
    )
    return sha256_text(text)
