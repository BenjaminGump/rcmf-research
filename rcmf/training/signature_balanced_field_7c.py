from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
import random
import statistics
from typing import Any, Mapping, Sequence

import torch
from torch import Tensor, nn
from torch.nn import functional as F


FIELD_FORMAT = "signature_balanced_lowrank_field_7c_v1"
LABEL_FORMAT = "clean_full_procedural_label_7c_v1"
CLASS_SCORE_FORMAT = "signature_class_score_7c_v1"


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def condition_semantic_key(condition: Mapping[str, Any]) -> str:
    """Hash exactly the inputs that determine a one-step Qwen prompt."""

    return canonical_hash(
        {
            "state_example_id": str(condition["state_example_id"]),
            "prompt_kind": str(condition["prompt_kind"]),
            "transition_id": (
                str(condition["transition_id"])
                if condition.get("transition_id") is not None
                else None
            ),
        }
    )


def select_scoreable_class_exemplar(
    *,
    class_row: Mapping[str, Any],
    legal_rows: Sequence[Mapping[str, Any]],
    transitions_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Select the canonical member or a deterministic same-class scoreable member."""

    member_ids = {str(value) for value in class_row["member_transition_ids"]}
    candidates = [
        row
        for row in legal_rows
        if str(row["transition_id"]) in member_ids
        and bool(row.get("scoreable_under_context", True))
    ]
    if not candidates:
        raise ValueError(
            f"Selected class has no legal scoreable member: "
            f"{class_row['signature_class_id']}"
        )
    canonical_id = str(class_row["canonical_transition_id"])
    by_id = {str(row["transition_id"]): row for row in candidates}
    if canonical_id in by_id:
        selected_id = canonical_id
        substituted = False
    else:
        class_token_counts = [
            int(transitions_by_id[transition_id]["teacher_section_tokens"])
            for transition_id in member_ids
        ]
        class_median = statistics.median(class_token_counts)
        selected_id = min(
            by_id,
            key=lambda transition_id: (
                abs(
                    int(
                        transitions_by_id[transition_id][
                            "teacher_section_tokens"
                        ]
                    )
                    - class_median
                ),
                hashlib.sha256(transition_id.encode("utf-8")).hexdigest(),
            ),
        )
        substituted = True
    return {
        "transition_id": selected_id,
        "canonical_transition_id": canonical_id,
        "scoreable_substitution": substituted,
        "selection_rule": (
            "canonical_clean_exemplar"
            if not substituted
            else "same_class_closest_to_class_median_teacher_tokens_then_sha256"
        ),
    }


def deterministic_seed(base: int, *parts: Any) -> int:
    payload = ":".join(str(value) for value in (base, *parts))
    return int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest()[:4], "big")


def state_class_balanced_weights(
    rows: Sequence[Mapping[str, Any]],
) -> list[float]:
    """Give every state and every legal signature class equal total mass."""

    positions_by_state_class: dict[tuple[str, str], list[int]] = defaultdict(list)
    classes_by_state: dict[str, set[str]] = defaultdict(set)
    for position, row in enumerate(rows):
        state_id = str(row["state_example_id"])
        class_id = str(row["signature_class_id"])
        positions_by_state_class[(state_id, class_id)].append(position)
        classes_by_state[state_id].add(class_id)
    if not classes_by_state:
        return []
    state_mass = 1.0 / len(classes_by_state)
    output = [0.0] * len(rows)
    for (state_id, class_id), positions in positions_by_state_class.items():
        class_mass = state_mass / len(classes_by_state[state_id])
        member_mass = class_mass / len(positions)
        for position in positions:
            output[position] = member_mass
    return output


def validate_class_balance(
    rows: Sequence[Mapping[str, Any]],
    weights: Sequence[float],
    *,
    tolerance: float = 1.0e-10,
) -> dict[str, Any]:
    if len(rows) != len(weights):
        raise ValueError("Rows and class-balance weights differ in length")
    state_class_mass: dict[str, dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    for row, weight in zip(rows, weights, strict=True):
        state_class_mass[str(row["state_example_id"])][
            str(row["signature_class_id"])
        ] += float(weight)
    state_mass = {
        state: sum(classes.values()) for state, classes in state_class_mass.items()
    }
    expected_state = 1.0 / len(state_mass) if state_mass else 0.0
    class_spreads = []
    for classes in state_class_mass.values():
        values = list(classes.values())
        class_spreads.append(max(values) - min(values) if values else 0.0)
    maximum_state_error = max(
        (abs(value - expected_state) for value in state_mass.values()), default=0.0
    )
    maximum_class_spread = max(class_spreads, default=0.0)
    total_mass_error = abs(sum(weights) - (1.0 if rows else 0.0))
    passed = bool(
        maximum_state_error <= tolerance
        and maximum_class_spread <= tolerance
        and total_mass_error <= tolerance
    )
    return {
        "passed": passed,
        "state_count": len(state_mass),
        "state_class_count": sum(len(value) for value in state_class_mass.values()),
        "expected_state_mass": expected_state,
        "maximum_state_mass_error": maximum_state_error,
        "maximum_within_state_class_mass_spread": maximum_class_spread,
        "total_mass": sum(weights),
        "total_mass_error": total_mass_error,
    }


class SignatureBalancedFieldSelector(nn.Module):
    """EXP-020 low-rank interaction branch without trainable popularity effects."""

    def __init__(
        self,
        *,
        state_views: int,
        transition_views: int,
        input_dim: int,
        projection_dim: int,
        interaction_rank: int,
    ) -> None:
        super().__init__()
        self.state_views = int(state_views)
        self.transition_views = int(transition_views)
        self.input_dim = int(input_dim)
        self.projection_dim = int(projection_dim)
        self.interaction_rank = int(interaction_rank)
        self.state_projection = nn.ModuleList(
            nn.Linear(input_dim, projection_dim, bias=False)
            for _ in range(state_views)
        )
        self.transition_projection = nn.ModuleList(
            nn.Linear(input_dim, projection_dim, bias=False)
            for _ in range(transition_views)
        )
        self.state_rank = nn.Linear(projection_dim, interaction_rank, bias=False)
        self.transition_rank = nn.Linear(
            projection_dim, interaction_rank, bias=False
        )
        self.tensor_core = nn.Parameter(
            torch.empty(state_views, transition_views, interaction_rank)
        )
        nn.init.normal_(self.tensor_core, std=1.0 / math.sqrt(interaction_rank))

    @staticmethod
    def _project(values: Tensor, projections: nn.ModuleList) -> Tensor:
        return torch.stack(
            [layer(values[:, index]) for index, layer in enumerate(projections)],
            dim=1,
        )

    def state_factors(self, state: Tensor) -> Tensor:
        return self.state_rank(self._project(state, self.state_projection))

    def transition_factors(self, transition: Tensor) -> Tensor:
        return self.transition_rank(
            self._project(transition, self.transition_projection)
        )

    def score_matrix(self, state: Tensor, transition: Tensor) -> Tensor:
        q = self.state_factors(state)
        k = self.transition_factors(transition)
        return torch.einsum("bvr,vwr,twr->bt", q, self.tensor_core, k) / math.sqrt(
            self.state_views * self.transition_views * self.interaction_rank
        )

    def forward(self, state: Tensor, transition: Tensor) -> Tensor:
        if state.shape[0] != transition.shape[0]:
            raise ValueError("Paired field scoring requires equal batch lengths")
        q = self.state_factors(state)
        k = self.transition_factors(transition)
        return torch.einsum("bvr,vwr,bwr->b", q, self.tensor_core, k) / math.sqrt(
            self.state_views * self.transition_views * self.interaction_rank
        )


@dataclass(frozen=True)
class ClassTarget:
    class_id: str
    member_indices: tuple[int, ...]
    mean_tier: float
    max_tier: int
    exact_api: float
    stage_compatible: float
    coarse_action_type: str


def class_targets_for_state(
    rows: Sequence[Mapping[str, Any]],
    transition_positions: Mapping[str, int],
) -> list[ClassTarget]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["signature_class_id"])].append(row)
    output = []
    for class_id in sorted(grouped):
        members = grouped[class_id]
        output.append(
            ClassTarget(
                class_id=class_id,
                member_indices=tuple(
                    transition_positions[str(row["transition_id"])] for row in members
                ),
                mean_tier=statistics.fmean(
                    float(row["procedural_tier"]) for row in members
                ),
                max_tier=max(int(row["procedural_tier"]) for row in members),
                exact_api=statistics.fmean(
                    float(bool(row["exact_api_sequence"])) for row in members
                ),
                stage_compatible=statistics.fmean(
                    float(bool(row["state_stage_compatible"])) for row in members
                ),
                coarse_action_type=str(members[0]["transition_coarse_action_type"]),
            )
        )
    return output


def aggregate_class_scores(
    transition_scores: Tensor,
    targets: Sequence[ClassTarget],
) -> Tensor:
    values = []
    for target in targets:
        index = torch.tensor(
            target.member_indices,
            dtype=torch.long,
            device=transition_scores.device,
        )
        values.append(transition_scores[index].mean())
    if not values:
        return transition_scores.new_empty((0,))
    return torch.stack(values)


def _deterministic_pairs(
    targets: Sequence[ClassTarget],
    *,
    maximum: int,
    seed: int,
    same_intent_only: bool,
) -> list[tuple[int, int, float]]:
    pairs = []
    for left in range(len(targets)):
        for right in range(left + 1, len(targets)):
            if same_intent_only and (
                targets[left].coarse_action_type
                != targets[right].coarse_action_type
            ):
                continue
            gap = targets[left].mean_tier - targets[right].mean_tier
            if abs(gap) < 1.0e-12:
                continue
            if gap > 0:
                pairs.append((left, right, min(abs(gap) / 4.0, 1.0)))
            else:
                pairs.append((right, left, min(abs(gap) / 4.0, 1.0)))
    if len(pairs) <= maximum:
        return pairs
    ordered = sorted(
        pairs,
        key=lambda value: (
            canonical_hash([seed, value[0], value[1], same_intent_only]),
            value,
        ),
    )
    return ordered[:maximum]


def procedural_field_objective(
    *,
    class_scores: Tensor,
    targets: Sequence[ClassTarget],
    temperature: float,
    pair_maximum: int,
    hard_maximum: int,
    seed: int,
    weights: Mapping[str, float],
) -> tuple[Tensor, dict[str, Tensor]]:
    if class_scores.numel() != len(targets):
        raise ValueError("Class scores and targets differ")
    target_tier = torch.tensor(
        [target.mean_tier for target in targets],
        dtype=class_scores.dtype,
        device=class_scores.device,
    )
    teacher = F.softmax(target_tier / float(temperature), dim=0)
    listwise = -(teacher * F.log_softmax(class_scores, dim=0)).sum()

    def pair_loss(
        pairs: Sequence[tuple[int, int, float]],
    ) -> Tensor:
        if not pairs:
            return class_scores.sum() * 0.0
        positive = torch.tensor(
            [value[0] for value in pairs],
            dtype=torch.long,
            device=class_scores.device,
        )
        negative = torch.tensor(
            [value[1] for value in pairs],
            dtype=torch.long,
            device=class_scores.device,
        )
        pair_weights = torch.tensor(
            [value[2] for value in pairs],
            dtype=class_scores.dtype,
            device=class_scores.device,
        )
        return (
            F.softplus(-(class_scores[positive] - class_scores[negative]))
            * pair_weights
        ).sum() / pair_weights.sum().clamp_min(1.0e-8)

    pairwise = pair_loss(
        _deterministic_pairs(
            targets,
            maximum=int(pair_maximum),
            seed=seed,
            same_intent_only=False,
        )
    )
    hard_negative = pair_loss(
        _deterministic_pairs(
            targets,
            maximum=int(hard_maximum),
            seed=seed,
            same_intent_only=True,
        )
    )
    exact_target = torch.tensor(
        [target.exact_api for target in targets],
        dtype=class_scores.dtype,
        device=class_scores.device,
    )
    stage_target = torch.tensor(
        [target.stage_compatible for target in targets],
        dtype=class_scores.dtype,
        device=class_scores.device,
    )
    exact_api = F.binary_cross_entropy_with_logits(class_scores, exact_target)
    stage = F.binary_cross_entropy_with_logits(class_scores, stage_target)
    losses = {
        "listwise": listwise,
        "pairwise": pairwise,
        "hard_negative": hard_negative,
        "exact_api": exact_api,
        "stage": stage,
    }
    total = sum(
        losses[name] * float(weights[name])
        for name in losses
    )
    return total, losses


def _rankdata(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = (start + end - 1) / 2.0 + 1.0
        for position in order[start:end]:
            ranks[position] = rank
        start = end
    return ranks


def pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum(
        (a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True)
    )
    left_norm = math.sqrt(sum((value - left_mean) ** 2 for value in left))
    right_norm = math.sqrt(sum((value - right_mean) ** 2 for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return None
    return numerator / (left_norm * right_norm)


def spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    return pearson(_rankdata(left), _rankdata(right))


def ndcg_at_k(relevance: Sequence[float], scores: Sequence[float], k: int) -> float:
    if not relevance:
        return 0.0
    cutoff = min(int(k), len(relevance))
    predicted = sorted(
        range(len(scores)), key=lambda index: (-scores[index], index)
    )[:cutoff]
    ideal = sorted(
        range(len(relevance)), key=lambda index: (-relevance[index], index)
    )[:cutoff]

    def dcg(indices: Sequence[int]) -> float:
        return sum(
            (2.0 ** float(relevance[index]) - 1.0) / math.log2(rank + 2.0)
            for rank, index in enumerate(indices)
        )

    denominator = dcg(ideal)
    return dcg(predicted) / denominator if denominator > 0.0 else 1.0


def _pairwise_accuracy(
    targets: Sequence[ClassTarget],
    scores: Sequence[float],
    *,
    same_intent_only: bool,
) -> float | None:
    correct = 0.0
    count = 0
    for left in range(len(targets)):
        for right in range(left + 1, len(targets)):
            if same_intent_only and (
                targets[left].coarse_action_type
                != targets[right].coarse_action_type
            ):
                continue
            gap = targets[left].mean_tier - targets[right].mean_tier
            if abs(gap) < 1.0e-12:
                continue
            prediction = scores[left] - scores[right]
            correct += float(prediction * gap > 0.0) + 0.5 * float(prediction == 0.0)
            count += 1
    return correct / count if count else None


def state_class_metrics(
    targets: Sequence[ClassTarget],
    scores: Sequence[float],
) -> dict[str, Any]:
    if len(targets) != len(scores):
        raise ValueError("Class targets and scores differ")
    relevance = [target.mean_tier for target in targets]
    ranked = sorted(range(len(scores)), key=lambda index: (-scores[index], index))
    best_relevance = max(relevance, default=0.0)
    best = {index for index, value in enumerate(relevance) if value == best_relevance}
    output: dict[str, Any] = {
        "class_count": len(targets),
        "spearman": spearman(relevance, list(scores)),
        "same_intent_pairwise_accuracy": _pairwise_accuracy(
            targets, scores, same_intent_only=True
        ),
        "top1_tier": targets[ranked[0]].max_tier if ranked else None,
        "top1_exact_api": bool(targets[ranked[0]].exact_api > 0.0) if ranked else None,
        "top1_class_id": targets[ranked[0]].class_id if ranked else None,
    }
    for cutoff in (1, 4, 8):
        selected = ranked[:cutoff]
        output[f"ndcg@{cutoff}"] = ndcg_at_k(relevance, list(scores), cutoff)
        output[f"tier34_recall@{cutoff}"] = float(
            any(targets[index].max_tier >= 3 for index in selected)
        )
        output[f"exact_api_recall@{cutoff}"] = float(
            any(targets[index].exact_api > 0.0 for index in selected)
        )
        output[f"best_class_recall@{cutoff}"] = float(
            any(index in best for index in selected)
        )
    return output


def summarize_state_metrics(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not rows:
        return {"state_count": 0, "task_count": 0}
    metric_names = (
        "ndcg@1",
        "ndcg@4",
        "ndcg@8",
        "tier34_recall@1",
        "tier34_recall@4",
        "tier34_recall@8",
        "exact_api_recall@1",
        "exact_api_recall@4",
        "exact_api_recall@8",
        "best_class_recall@1",
        "best_class_recall@4",
        "best_class_recall@8",
        "spearman",
        "same_intent_pairwise_accuracy",
    )
    output: dict[str, Any] = {
        "state_count": len(rows),
        "task_count": len({str(row["task_id"]) for row in rows}),
    }
    for name in metric_names:
        values = [float(row[name]) for row in rows if row.get(name) is not None]
        output[name] = statistics.fmean(values) if values else None
        output[f"{name}_count"] = len(values)
    output["top1_tier34_coverage"] = statistics.fmean(
        float(int(row["top1_tier"]) >= 3) for row in rows
    )
    output["top1_exact_api_coverage"] = statistics.fmean(
        float(bool(row["top1_exact_api"])) for row in rows
    )
    per_task: dict[str, Any] = {}
    for task_id in sorted({str(row["task_id"]) for row in rows}):
        selected = [row for row in rows if str(row["task_id"]) == task_id]
        per_task[task_id] = {
            name: (
                statistics.fmean(
                    float(row[name]) for row in selected if row.get(name) is not None
                )
                if any(row.get(name) is not None for row in selected)
                else None
            )
            for name in metric_names
        }
        per_task[task_id]["state_count"] = len(selected)
    output["per_task"] = per_task
    return output


def grouped_task_parent_folds(
    task_ids: Sequence[str],
    parent_ids: Sequence[str],
    *,
    fold_count: int,
    seed: int,
) -> list[dict[str, set[str]]]:
    tasks = sorted(
        set(task_ids),
        key=lambda value: (canonical_hash([seed, "task", value]), value),
    )
    parents = sorted(
        set(parent_ids),
        key=lambda value: (canonical_hash([seed, "parent", value]), value),
    )
    folds = []
    for fold in range(int(fold_count)):
        folds.append(
            {
                "heldout_tasks": {
                    value for index, value in enumerate(tasks) if index % fold_count == fold
                },
                "heldout_parents": {
                    value
                    for index, value in enumerate(parents)
                    if index % fold_count == fold
                },
            }
        )
    return folds


def task_grouped_bootstrap_difference(
    correct_rows: Sequence[Mapping[str, Any]],
    control_rows: Sequence[Mapping[str, Any]],
    *,
    metric: str,
    samples: int,
    seed: int,
) -> dict[str, Any]:
    correct = {str(row["state_example_id"]): row for row in correct_rows}
    control = {str(row["state_example_id"]): row for row in control_rows}
    if set(correct) != set(control):
        raise ValueError("Bootstrap rows do not share state identities")
    task_states: dict[str, list[str]] = defaultdict(list)
    for state_id, row in correct.items():
        task_states[str(row["task_id"])].append(state_id)
    tasks = sorted(task_states)
    task_effect = {
        task: statistics.fmean(
            float(correct[state][metric]) - float(control[state][metric])
            for state in task_states[task]
        )
        for task in tasks
    }
    observed = statistics.fmean(task_effect.values()) if task_effect else None
    if not tasks or samples <= 0:
        return {
            "metric": metric,
            "task_count": len(tasks),
            "observed_difference": observed,
            "ci95": None,
        }
    generator = random.Random(int(seed))
    draws = []
    for _ in range(int(samples)):
        sampled = [generator.choice(tasks) for _ in tasks]
        draws.append(statistics.fmean(task_effect[task] for task in sampled))
    draws.sort()
    lower = draws[max(0, int(0.025 * len(draws)))]
    upper = draws[min(len(draws) - 1, int(0.975 * len(draws)))]
    return {
        "metric": metric,
        "task_count": len(tasks),
        "observed_difference": observed,
        "ci95": [lower, upper],
        "samples": int(samples),
        "seed": int(seed),
    }


def class_selection_diversity(
    state_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    classes = [str(row["top1_class_id"]) for row in state_rows]
    class_counts = Counter(classes)
    parents = [str(row.get("selected_parent_id")) for row in state_rows]
    tasks = [str(row.get("selected_source_task_id")) for row in state_rows]
    documentation = [bool(row.get("selected_api_documentation")) for row in state_rows]
    return {
        "selection_count": len(state_rows),
        "unique_class_count": len(class_counts),
        "maximum_class_selection_fraction": (
            max(class_counts.values()) / len(classes) if classes else 0.0
        ),
        "unique_parent_count": len(set(parents)),
        "unique_source_task_count": len(set(tasks)),
        "api_documentation_fraction": (
            statistics.fmean(float(value) for value in documentation)
            if documentation
            else 0.0
        ),
        "class_counts": dict(sorted(class_counts.items())),
    }


def _targets_by_state(
    rows: Sequence[Mapping[str, Any]],
    transition_positions: Mapping[str, int],
) -> dict[str, list[ClassTarget]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["state_example_id"])].append(row)
    return {
        state_id: class_targets_for_state(values, transition_positions)
        for state_id, values in grouped.items()
    }


def train_field_selector(
    *,
    model: SignatureBalancedFieldSelector,
    rows: Sequence[Mapping[str, Any]],
    state_representations: Tensor,
    transition_representations: Tensor,
    ordered_state_ids: Sequence[str],
    ordered_transition_ids: Sequence[str],
    candidate: Mapping[str, Any],
    batch_states: int,
    maximum_pair_samples_per_state: int,
    maximum_hard_samples_per_state: int,
    weight_decay: float,
    seed: int,
    device: torch.device,
    resume: Mapping[str, Any] | None = None,
    checkpoint_callback: Any | None = None,
    checkpoint_interval_epochs: int = 10,
) -> dict[str, Any]:
    """Train only the field interaction, with one equal-weight loss per state."""

    state_position = {str(value): index for index, value in enumerate(ordered_state_ids)}
    transition_position = {
        str(value): index for index, value in enumerate(ordered_transition_ids)
    }
    targets = _targets_by_state(rows, transition_position)
    state_ids = sorted(targets)
    missing = [state_id for state_id in state_ids if state_id not in state_position]
    if missing:
        raise ValueError(f"Training states are absent from representations: {missing[:3]}")
    if not state_ids:
        raise ValueError("No state-grouped procedural rows were provided")
    torch.manual_seed(int(seed))
    random.seed(int(seed))
    model.to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(candidate["learning_rate"]),
        weight_decay=float(weight_decay),
    )
    start_epoch = 0
    optimizer_updates = 0
    history: list[dict[str, Any]] = []
    if resume is not None:
        model.load_state_dict(resume["model_state_dict"])
        optimizer.load_state_dict(resume["optimizer_state_dict"])
        start_epoch = int(resume["completed_epochs"])
        optimizer_updates = int(resume["optimizer_updates"])
        history = list(resume.get("history", []))
        if list(resume["ordered_state_ids"]) != state_ids:
            raise ValueError("Resume state order differs")
        if list(resume["ordered_transition_ids"]) != list(ordered_transition_ids):
            raise ValueError("Resume transition order differs")
    transition_values = transition_representations.to(device, dtype=torch.float32)
    loss_weights = {
        name: float(candidate[f"{name}_weight"])
        for name in ("listwise", "pairwise", "hard_negative", "exact_api", "stage")
    }
    epochs = int(candidate["epochs"])
    for epoch in range(start_epoch + 1, epochs + 1):
        model.train()
        epoch_generator = torch.Generator().manual_seed(
            deterministic_seed(seed, "epoch-order", epoch)
        )
        order = torch.randperm(
            len(state_ids), generator=epoch_generator
        ).tolist()
        epoch_totals: dict[str, float] = defaultdict(float)
        epoch_batches = 0
        epoch_gradient_norm = 0.0
        for start in range(0, len(order), int(batch_states)):
            positions = order[start : start + int(batch_states)]
            selected_ids = [state_ids[position] for position in positions]
            state_values = torch.stack(
                [
                    state_representations[state_position[state_id]]
                    for state_id in selected_ids
                ]
            ).to(device=device, dtype=torch.float32)
            transition_scores = model.score_matrix(state_values, transition_values)
            losses = []
            component_values: dict[str, list[Tensor]] = defaultdict(list)
            for batch_position, state_id in enumerate(selected_ids):
                class_scores = aggregate_class_scores(
                    transition_scores[batch_position], targets[state_id]
                )
                loss, components = procedural_field_objective(
                    class_scores=class_scores,
                    targets=targets[state_id],
                    temperature=float(candidate["temperature"]),
                    pair_maximum=int(maximum_pair_samples_per_state),
                    hard_maximum=int(maximum_hard_samples_per_state),
                    seed=deterministic_seed(seed, epoch, state_id),
                    weights=loss_weights,
                )
                losses.append(loss)
                for name, value in components.items():
                    component_values[name].append(value)
            # Keep each state at one fixed unit of gradient mass even when the
            # final batch is shorter than the configured batch size.
            batch_loss = torch.stack(losses).sum() / float(batch_states)
            optimizer.zero_grad(set_to_none=True)
            batch_loss.backward()
            gradient_norm = float(
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                .detach()
                .cpu()
            )
            optimizer.step()
            optimizer_updates += 1
            epoch_batches += 1
            epoch_gradient_norm += gradient_norm
            epoch_totals["total"] += float(batch_loss.detach().cpu())
            for name, values in component_values.items():
                epoch_totals[name] += float(torch.stack(values).mean().detach().cpu())
        if (
            epoch == start_epoch + 1
            or epoch % int(checkpoint_interval_epochs) == 0
            or epoch == epochs
        ):
            row = {
                "epoch": epoch,
                "optimizer_updates": optimizer_updates,
                "mean_gradient_norm": epoch_gradient_norm / max(epoch_batches, 1),
                **{
                    f"mean_{name}_loss": value / max(epoch_batches, 1)
                    for name, value in sorted(epoch_totals.items())
                },
            }
            history.append(row)
            if checkpoint_callback is not None:
                checkpoint_callback(
                    {
                        "format": FIELD_FORMAT,
                        "model_state_dict": {
                            key: value.detach().cpu()
                            for key, value in model.state_dict().items()
                        },
                        "optimizer_state_dict": optimizer.state_dict(),
                        "completed_epochs": epoch,
                        "optimizer_updates": optimizer_updates,
                        "history": history,
                        "ordered_state_ids": state_ids,
                        "ordered_transition_ids": list(ordered_transition_ids),
                        "candidate": dict(candidate),
                        "seed": int(seed),
                    }
                )
    model.eval()
    return {
        "completed_epochs": epochs,
        "optimizer_updates": optimizer_updates,
        "history": history,
        "state_count": len(state_ids),
        "transition_count": len(ordered_transition_ids),
        "state_class_count": sum(len(value) for value in targets.values()),
        "model_state_dict": {
            key: value.detach().cpu() for key, value in model.state_dict().items()
        },
        "optimizer_state_dict": optimizer.state_dict(),
    }


@torch.no_grad()
def score_field_selector(
    *,
    model: SignatureBalancedFieldSelector,
    state_representations: Tensor,
    transition_representations: Tensor,
    batch_states: int,
    device: torch.device,
) -> Tensor:
    model.to(device).eval()
    transition = transition_representations.to(device, dtype=torch.float32)
    output = []
    for start in range(0, len(state_representations), int(batch_states)):
        state = state_representations[start : start + int(batch_states)].to(
            device, dtype=torch.float32
        )
        output.append(model.score_matrix(state, transition).detach().cpu())
    return torch.cat(output, dim=0)


def calibrated_ensemble(
    train_score_matrices: Sequence[Tensor],
    score_matrices: Sequence[Tensor],
) -> tuple[Tensor, list[dict[str, float]]]:
    if len(train_score_matrices) != len(score_matrices) or not score_matrices:
        raise ValueError("Ensemble score collections differ or are empty")
    calibrated = []
    statistics_rows = []
    for train, values in zip(train_score_matrices, score_matrices, strict=True):
        mean = float(train.mean())
        std = float(train.std(unbiased=False).clamp_min(1.0e-8))
        calibrated.append((values - mean) / std)
        statistics_rows.append({"train_mean": mean, "train_std": std})
    return torch.stack(calibrated, dim=0).mean(dim=0), statistics_rows


def evaluate_score_matrix(
    *,
    rows: Sequence[Mapping[str, Any]],
    scores: Tensor,
    ordered_state_ids: Sequence[str],
    ordered_transition_ids: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    state_position = {str(value): index for index, value in enumerate(ordered_state_ids)}
    transition_position = {
        str(value): index for index, value in enumerate(ordered_transition_ids)
    }
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["state_example_id"])].append(row)
    output = []
    for state_id in sorted(grouped):
        state_rows = grouped[state_id]
        targets = class_targets_for_state(state_rows, transition_position)
        class_scores = aggregate_class_scores(
            scores[state_position[state_id]], targets
        ).tolist()
        metrics = state_class_metrics(targets, class_scores)
        output.append(
            {
                "format": CLASS_SCORE_FORMAT,
                "state_example_id": state_id,
                "task_id": str(state_rows[0]["state_task_id"]),
                "cell": str(state_rows[0]["cell"]),
                **metrics,
                "ranked_classes": [
                    {
                        "signature_class_id": targets[index].class_id,
                        "score": float(class_scores[index]),
                        "mean_tier": float(targets[index].mean_tier),
                        "max_tier": int(targets[index].max_tier),
                        "exact_api": float(targets[index].exact_api),
                        "stage_compatible": float(targets[index].stage_compatible),
                        "coarse_action_type": targets[index].coarse_action_type,
                    }
                    for index in sorted(
                        range(len(targets)),
                        key=lambda index: (-class_scores[index], targets[index].class_id),
                    )
                ],
            }
        )
    return output, summarize_state_metrics(output)
