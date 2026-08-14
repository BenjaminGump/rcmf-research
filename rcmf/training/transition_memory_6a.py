from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import math
import random
from typing import Any, Iterable, Mapping, Sequence

import torch
from torch import Tensor

from rcmf.benchmarks.appworld.prompt import appworld_renderer_metadata
from rcmf.benchmarks.appworld.transitions import (
    API_CALL_RE,
    DecisionTransition,
    transition_teacher_section,
)
from rcmf.schemas import DecisionExample
from rcmf.training.addressing_4b import _pearson, mean_std
from rcmf.training.pair_grounding_5d import spearman


TRANSITION_EXPERIMENT_VERSION = "decision_transition_memory_feasibility_6a_v1"
TRANSITION_QUERY_MANIFEST_VERSION = "decision_transition_query_manifest_6a_v1"
TRANSITION_PREFLIGHT_VERSION = "decision_transition_teacher_preflight_6a_v1"
TRANSITION_TEACHER_CACHE_VERSION = "decision_transition_raw_teacher_cache_6a_v1"
TRANSITION_RESPONSE_CACHE_VERSION = "decision_transition_response_cache_6a_v1"
TRANSITION_PAIR_ORACLE_VERSION = "decision_transition_pair_oracle_6a_v1"
TRANSITION_STATIC_PROGRAM_VERSION = "decision_transition_static_program_6a_v1"
TRAJECTORY_STATIC_BASELINE_VERSION = "whole_trajectory_static_program_baseline_6a_v1"

UTILITY_NEUTRAL_EPS = 0.01

LEAKAGE_METADATA_FIELDS = {
    "task": ("task_id", "source_task_id", "original_task_id", "parent_task_id"),
    "episode": (
        "episode_id",
        "source_episode_id",
        "original_episode_id",
        "parent_episode_id",
        "derived_from_episode_id",
    ),
    "replay": (
        "replay_id",
        "source_replay_id",
        "original_replay_id",
        "parent_replay_id",
        "derived_from_replay_id",
    ),
    "lineage": (
        "lineage_id",
        "source_lineage_id",
        "original_lineage_id",
        "parent_lineage_id",
        "derived_from",
        "derived_from_id",
        "trace_id",
    ),
}


def canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def state_example_id(index: int, example: DecisionExample) -> str:
    return f"{example.episode_id}:step:{example.step_id}:line:{index + 1}"


def example_task_id(example: DecisionExample) -> str:
    return str(example.metadata.get("task_id") or example.episode_id.rsplit(":", 1)[-1])


def _iter_metadata_values(metadata: Mapping[str, Any], fields: Sequence[str]) -> list[str]:
    output: list[str] = []

    def add(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, dict):
            for key in (
                "id",
                "task_id",
                "episode_id",
                "replay_id",
                "lineage_id",
                "source_episode_id",
            ):
                add(value.get(key))
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                add(item)
            return
        text = str(value).strip()
        if text:
            output.append(text)

    for field in fields:
        add(metadata.get(field))
    return output


def example_leakage_keys(example: DecisionExample) -> set[str]:
    keys = {
        f"task:{example_task_id(example)}",
        f"episode:{example.episode_id}",
    }
    for category, fields in LEAKAGE_METADATA_FIELDS.items():
        keys.update(
            f"{category}:{value}"
            for value in _iter_metadata_values(example.metadata, fields)
        )
    return keys


def transition_leakage_keys(transition: Mapping[str, Any] | DecisionTransition) -> set[str]:
    if isinstance(transition, DecisionTransition):
        return transition.leakage_keys
    if "leakage_keys" in transition:
        return {str(value) for value in transition["leakage_keys"]}
    keys = {
        f"task:{transition['parent_task_id']}",
        f"episode:{transition['parent_episode_id']}",
    }
    keys.update(f"task:{value}" for value in transition.get("parent_task_ids", []))
    keys.update(
        f"episode:{value}" for value in transition.get("parent_episode_ids", [])
    )
    keys.update(f"replay:{value}" for value in transition.get("parent_replay_ids", []))
    keys.update(
        f"lineage:{value}" for value in transition.get("parent_lineage_ids", [])
    )
    return keys


def is_legal_transition_pair(
    example: DecisionExample,
    transition: Mapping[str, Any] | DecisionTransition,
) -> bool:
    return example_leakage_keys(example).isdisjoint(transition_leakage_keys(transition))


def decoder_manifest_state_ids(manifest: Mapping[str, Any]) -> set[str]:
    pair_ids: set[str] = set()
    for key in ("ordered_source_pair_ids", "ordered_pair_ids"):
        pair_ids.update(str(value) for value in manifest.get(key, []))
    for fold in manifest.get("folds", []):
        pair_ids.update(str(value) for value in fold.get("train_pair_ids", []))
        pair_ids.update(str(value) for value in fold.get("heldout_pair_ids", []))
    state_ids = {
        pair_id.split("::memory::", 1)[0]
        for pair_id in pair_ids
        if "::memory::" in pair_id
    }
    expected = manifest.get("state_count")
    if expected is not None and len(state_ids) != int(expected):
        raise ValueError(
            f"EXP-016C decoder manifest state count differs: {len(state_ids)} != {expected}"
        )
    return state_ids


def _quantile(values: Sequence[int], fraction: float) -> int:
    ordered = sorted(int(value) for value in values)
    if not ordered:
        return 0
    index = int(math.floor((len(ordered) - 1) * fraction + 0.5))
    return ordered[index]


def _length_bucket(value: int, q1: int, q2: int) -> str:
    if value <= q1:
        return "short"
    if value <= q2:
        return "medium"
    return "long"


def _task_family(task_id: str) -> str:
    return task_id.rsplit("_", 1)[0]


def _stable_order_key(seed: int, namespace: str, value: str) -> str:
    return hashlib.sha256(f"{seed}:{namespace}:{value}".encode("utf-8")).hexdigest()


def _task_apps(examples: Sequence[DecisionExample]) -> dict[str, set[str]]:
    output: dict[str, set[str]] = defaultdict(set)
    for example in examples:
        task_id = example_task_id(example)
        output[task_id].update(
            app
            for app, _ in API_CALL_RE.findall(example.target_text)
            if app not in {"api_docs", "supervisor"}
        )
    return output


def _query_candidates_by_task(
    *,
    examples: Sequence[DecisionExample],
    prompt_token_counts: Sequence[int],
    allowed_task_ids: set[str],
    excluded_state_ids: set[str],
    q1: int,
    q2: int,
) -> dict[str, dict[str, Any]]:
    rows_by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    max_step: dict[str, int] = defaultdict(int)
    for example in examples:
        max_step[example_task_id(example)] = max(
            max_step[example_task_id(example)], int(example.step_id)
        )
    for index, example in enumerate(examples):
        task_id = example_task_id(example)
        state_id = state_example_id(index, example)
        if task_id not in allowed_task_ids or state_id in excluded_state_ids:
            continue
        rows_by_task[task_id].append(
            {
                "example_index": index,
                "state_example_id": state_id,
                "step_id": int(example.step_id),
                "step_count": int(max_step[task_id]),
                "step_ratio": (
                    0.0
                    if max_step[task_id] <= 1
                    else (int(example.step_id) - 1) / (max_step[task_id] - 1)
                ),
                "prompt_tokens": int(prompt_token_counts[index]),
                "prompt_length_bucket": _length_bucket(
                    int(prompt_token_counts[index]), q1, q2
                ),
            }
        )
    apps = _task_apps(examples)
    output: dict[str, dict[str, Any]] = {}
    for task_id, rows in rows_by_task.items():
        ordered = sorted(rows, key=lambda row: (row["step_id"], row["example_index"]))
        if len(ordered) < 2:
            continue
        early = min(
            ordered,
            key=lambda row: (
                abs(float(row["step_ratio"]) - 1.0 / 3.0),
                row["step_id"],
            ),
        )
        later = min(
            (row for row in ordered if row["example_index"] != early["example_index"]),
            key=lambda row: (
                abs(float(row["step_ratio"]) - 2.0 / 3.0),
                -row["step_id"],
            ),
        )
        if early["step_id"] > later["step_id"]:
            early, later = later, early
        output[task_id] = {
            "task_id": task_id,
            "task_family": _task_family(task_id),
            "apps": sorted(apps.get(task_id, set())) or ["unknown"],
            "states": [
                {**early, "selection_role": "earlier"},
                {**later, "selection_role": "later"},
            ],
        }
    return output


def _select_tasks_greedily(
    candidates: Mapping[str, dict[str, Any]],
    *,
    count: int,
    seed: int,
    namespace: str,
) -> list[dict[str, Any]]:
    if len(candidates) < count:
        raise ValueError(f"Only {len(candidates)} eligible {namespace} tasks; need {count}")
    remaining = dict(candidates)
    selected: list[dict[str, Any]] = []
    used_families: Counter[str] = Counter()
    used_apps: Counter[str] = Counter()
    used_lengths: Counter[str] = Counter()
    while len(selected) < count:
        ranked: list[tuple[tuple[Any, ...], str, dict[str, Any]]] = []
        for task_id, candidate in remaining.items():
            family = str(candidate["task_family"])
            apps = [str(value) for value in candidate["apps"]]
            lengths = [str(row["prompt_length_bucket"]) for row in candidate["states"]]
            score = (
                int(used_families[family] == 0),
                sum(used_apps[app] == 0 for app in apps),
                sum(used_lengths[bucket] == 0 for bucket in lengths),
                -used_families[family],
                -sum(used_apps[app] for app in apps),
                _stable_order_key(seed, namespace, task_id),
            )
            ranked.append((score, task_id, candidate))
        _, task_id, chosen = max(ranked, key=lambda item: item[0])
        selected.append(chosen)
        used_families[str(chosen["task_family"])] += 1
        used_apps.update(str(value) for value in chosen["apps"])
        used_lengths.update(
            str(row["prompt_length_bucket"]) for row in chosen["states"]
        )
        del remaining[task_id]
    return selected


def select_query_manifest(
    *,
    examples: Sequence[DecisionExample],
    prompt_token_counts: Sequence[int],
    split_manifest: Mapping[str, Any],
    decoder_manifest: Mapping[str, Any],
    seed: int,
) -> dict[str, Any]:
    if len(examples) != len(prompt_token_counts):
        raise ValueError("prompt token counts must align one-to-one with examples")
    train_tasks = {str(value) for value in split_manifest["train_task_ids"]}
    validation_tasks = {
        str(value) for value in split_manifest["validation_task_ids"]
    }
    if len(train_tasks) != 37 or len(validation_tasks) != 9:
        raise ValueError(
            f"Locked split differs: train={len(train_tasks)} validation={len(validation_tasks)}"
        )
    excluded = decoder_manifest_state_ids(decoder_manifest)
    q1 = _quantile(prompt_token_counts, 1.0 / 3.0)
    q2 = _quantile(prompt_token_counts, 2.0 / 3.0)
    train_candidates = _query_candidates_by_task(
        examples=examples,
        prompt_token_counts=prompt_token_counts,
        allowed_task_ids=train_tasks,
        excluded_state_ids=excluded,
        q1=q1,
        q2=q2,
    )
    validation_candidates = _query_candidates_by_task(
        examples=examples,
        prompt_token_counts=prompt_token_counts,
        allowed_task_ids=validation_tasks,
        excluded_state_ids=excluded,
        q1=q1,
        q2=q2,
    )
    selected_train = _select_tasks_greedily(
        train_candidates, count=12, seed=seed, namespace="train"
    )
    selected_validation = _select_tasks_greedily(
        validation_candidates, count=4, seed=seed, namespace="validation"
    )
    rows: list[dict[str, Any]] = []
    for split, tasks in (("train", selected_train), ("validation", selected_validation)):
        for task in tasks:
            for state in task["states"]:
                rows.append(
                    {
                        **state,
                        "split": split,
                        "task_id": task["task_id"],
                        "task_family": task["task_family"],
                        "apps": task["apps"],
                    }
                )
    rows.sort(key=lambda row: (row["split"], row["task_id"], row["step_id"]))
    selected_ids = {str(row["state_example_id"]) for row in rows}
    overlap = sorted(selected_ids.intersection(excluded))
    manifest_payload = {
        "format": TRANSITION_QUERY_MANIFEST_VERSION,
        "seed": int(seed),
        "selection_definition": (
            "task_grouped_12x2_train_4x2_validation_greedy_family_app_length_"
            "coverage_with_one_third_two_thirds_state_roles_v1"
        ),
        "train_task_ids": sorted(
            {str(row["task_id"]) for row in rows if row["split"] == "train"}
        ),
        "validation_task_ids": sorted(
            {
                str(row["task_id"])
                for row in rows
                if row["split"] == "validation"
            }
        ),
        "query_rows": rows,
        "query_count": len(rows),
        "train_query_count": sum(row["split"] == "train" for row in rows),
        "validation_query_count": sum(
            row["split"] == "validation" for row in rows
        ),
        "prompt_length_quantiles": {"q1": q1, "q2": q2},
        "prompt_length_bucket_counts": dict(
            Counter(str(row["prompt_length_bucket"]) for row in rows)
        ),
        "selection_role_counts": dict(
            Counter(str(row["selection_role"]) for row in rows)
        ),
        "app_counts": dict(
            Counter(app for row in rows for app in row.get("apps", []))
        ),
        "excluded_exp016c_state_count": len(excluded),
        "excluded_exp016c_state_ids_sha256": canonical_json_sha256(sorted(excluded)),
        "selected_exp016c_overlap": overlap,
        "source_split_manifest_sha256": canonical_json_sha256(split_manifest),
        "source_decoder_manifest_sha256": canonical_json_sha256(decoder_manifest),
    }
    manifest_payload["manifest_sha256"] = canonical_json_sha256(manifest_payload)
    if len(rows) != 32 or overlap:
        raise ValueError(
            f"Invalid query manifest: count={len(rows)} EXP-016C overlap={overlap[:5]}"
        )
    return manifest_payload


def messages_with_transition_memory(
    base_messages: Sequence[dict[str, str]],
    transition: Mapping[str, Any] | DecisionTransition,
    prompt_profile: str,
) -> list[dict[str, str]]:
    messages = [dict(message) for message in base_messages]
    initial_count = int(
        appworld_renderer_metadata(prompt_profile)["initial_message_count"]
    )
    section = transition_teacher_section(transition)
    for index in range(initial_count, len(messages)):
        if messages[index].get("role") == "user":
            messages[index]["content"] = (
                f"{section}\n\n"
                "[CURRENT APPWORLD STATE START]\n"
                f"{messages[index]['content']}\n"
                "[CURRENT APPWORLD STATE END]"
            )
            return messages
    raise ValueError("Could not locate current task user message for transition insertion")


def utility_category(value: float, eps: float = UTILITY_NEUTRAL_EPS) -> str:
    if value > eps:
        return "positive"
    if value < -eps:
        return "negative"
    return "neutral"


def transition_step_bucket(step_index: int, step_count: int) -> str:
    if step_count <= 1:
        return "early"
    ratio = (step_index - 1) / (step_count - 1)
    if ratio <= 1.0 / 3.0:
        return "early"
    if ratio <= 2.0 / 3.0:
        return "middle"
    return "late"


def _coverage_select(
    rows: Sequence[dict[str, Any]],
    *,
    count: int,
    seed: int,
    namespace: str,
) -> list[dict[str, Any]]:
    remaining = {str(row["pair_id"]): row for row in rows}
    chosen: list[dict[str, Any]] = []
    transitions: Counter[str] = Counter()
    parents: Counter[str] = Counter()
    queries: Counter[str] = Counter()
    apps: Counter[str] = Counter()
    while remaining and len(chosen) < count:
        ranked = []
        for pair_id, row in remaining.items():
            row_apps = [str(value) for value in row.get("transition_apps", [])]
            score = (
                int(transitions[str(row["transition_id"])] == 0),
                int(parents[str(row["parent_memory_id"])] == 0),
                int(queries[str(row["state_example_id"])] == 0),
                sum(apps[app] == 0 for app in row_apps),
                -transitions[str(row["transition_id"])],
                -parents[str(row["parent_memory_id"])],
                _stable_order_key(seed, namespace, pair_id),
            )
            ranked.append((score, pair_id, row))
        _, pair_id, row = max(ranked, key=lambda item: item[0])
        chosen.append(row)
        transitions[str(row["transition_id"])] += 1
        parents[str(row["parent_memory_id"])] += 1
        queries[str(row["state_example_id"])] += 1
        apps.update(str(value) for value in row.get("transition_apps", []))
        del remaining[pair_id]
    return chosen


def select_pair_oracle_subset(
    rows: Sequence[dict[str, Any]],
    *,
    seed: int,
    per_category: int = 16,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scored = [row for row in rows if row.get("valid_for_loss")]
    by_utility = {
        category: [
            row
            for row in scored
            if utility_category(float(row["text_utility"])) == category
        ]
        for category in ("positive", "neutral", "negative")
    }
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    available = {key: len(value) for key, value in by_utility.items()}
    for category in ("positive", "neutral", "negative"):
        chosen = _coverage_select(
            by_utility[category],
            count=per_category,
            seed=seed,
            namespace=category,
        )
        if len(chosen) != per_category:
            raise ValueError(
                f"Only {len(chosen)} {category} pairs available; need {per_category}"
            )
        for row in chosen:
            selected.append({**row, "selection_category": category})
            selected_ids.add(str(row["pair_id"]))
    random_pool = [row for row in scored if str(row["pair_id"]) not in selected_ids]
    random_chosen = _coverage_select(
        random_pool,
        count=per_category,
        seed=seed,
        namespace="random",
    )
    if len(random_chosen) != per_category:
        raise ValueError(f"Only {len(random_chosen)} random pairs available")
    selected.extend({**row, "selection_category": "random"} for row in random_chosen)
    selected.sort(
        key=lambda row: (
            str(row["selection_category"]),
            str(row["transition_id"]),
            str(row["state_example_id"]),
        )
    )
    report = {
        "format": "decision_transition_pair_oracle_subset_6a_v1",
        "seed": seed,
        "count": len(selected),
        "available_utility_categories": available,
        "selection_category_counts": dict(
            Counter(str(row["selection_category"]) for row in selected)
        ),
        "unique_transition_count": len(
            {str(row["transition_id"]) for row in selected}
        ),
        "unique_parent_count": len(
            {str(row["parent_memory_id"]) for row in selected}
        ),
        "unique_query_count": len(
            {str(row["state_example_id"]) for row in selected}
        ),
        "pair_ids": [str(row["pair_id"]) for row in selected],
    }
    report["manifest_sha256"] = canonical_json_sha256(report)
    return selected, report


def select_static_transitions(
    teacher_rows: Sequence[dict[str, Any]],
    transition_by_id: Mapping[str, Mapping[str, Any]],
    *,
    seed: int,
    count: int = 24,
    minimum_parents: int = 12,
) -> tuple[list[str], dict[str, Any]]:
    train_rows = [
        row
        for row in teacher_rows
        if row.get("valid_for_loss") and str(row.get("split")) == "train"
    ]
    by_transition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in train_rows:
        by_transition[str(row["transition_id"])].append(row)
    candidates: list[dict[str, Any]] = []
    for transition_id, rows in by_transition.items():
        if len(rows) < 2:
            continue
        transition = transition_by_id[transition_id]
        categories = Counter(
            utility_category(float(row["text_utility"])) for row in rows
        )
        candidates.append(
            {
                "transition_id": transition_id,
                "parent_memory_id": str(transition["parent_memory_id"]),
                "step_bucket": transition_step_bucket(
                    int(transition["step_index"]), int(transition["step_count"])
                ),
                "apps": [str(value) for value in transition.get("apps", [])]
                or ["unknown"],
                "observation_count": len(rows),
                "utility_category_counts": dict(categories),
                "mixed_sign_pattern": bool(
                    categories.get("positive", 0)
                    and (categories.get("neutral", 0) or categories.get("negative", 0))
                ),
            }
        )
    if len(candidates) < count:
        raise ValueError(f"Only {len(candidates)} static-transition candidates; need {count}")
    remaining = {row["transition_id"]: row for row in candidates}
    selected: list[dict[str, Any]] = []
    parents: Counter[str] = Counter()
    buckets: Counter[str] = Counter()
    apps: Counter[str] = Counter()
    patterns: Counter[bool] = Counter()
    while remaining and len(selected) < count:
        ranked = []
        for transition_id, row in remaining.items():
            score = (
                int(parents[row["parent_memory_id"]] == 0),
                int(buckets[row["step_bucket"]] == 0),
                sum(apps[app] == 0 for app in row["apps"]),
                int(patterns[bool(row["mixed_sign_pattern"])] == 0),
                int(row["mixed_sign_pattern"]),
                min(int(row["observation_count"]), 24),
                _stable_order_key(seed, "static", transition_id),
            )
            ranked.append((score, transition_id, row))
        _, transition_id, row = max(ranked, key=lambda item: item[0])
        selected.append(row)
        parents[row["parent_memory_id"]] += 1
        buckets[row["step_bucket"]] += 1
        apps.update(row["apps"])
        patterns[bool(row["mixed_sign_pattern"])] += 1
        del remaining[transition_id]
    parent_count = len({row["parent_memory_id"] for row in selected})
    if len(selected) != count or parent_count < minimum_parents:
        raise ValueError(
            f"Static selection failed coverage: transitions={len(selected)} parents={parent_count}"
        )
    report = {
        "format": "decision_transition_static_selection_6a_v1",
        "seed": seed,
        "selection_uses_train_rows_only": True,
        "transition_count": len(selected),
        "parent_count": parent_count,
        "transition_ids": [str(row["transition_id"]) for row in selected],
        "parent_memory_ids": sorted(
            {str(row["parent_memory_id"]) for row in selected}
        ),
        "step_bucket_counts": dict(Counter(row["step_bucket"] for row in selected)),
        "app_counts": dict(Counter(app for row in selected for app in row["apps"])),
        "mixed_pattern_count": sum(bool(row["mixed_sign_pattern"]) for row in selected),
        "details": selected,
    }
    report["manifest_sha256"] = canonical_json_sha256(report)
    return report["transition_ids"], report


class TransitionAssociativeField:
    def __init__(self, rank: int, program_dim: int, *, dtype: torch.dtype = torch.float64) -> None:
        self.rank = int(rank)
        self.program_dim = int(program_dim)
        self.dtype = dtype
        self.V = torch.zeros(self.rank, self.program_dim, dtype=dtype)
        self.G = torch.zeros(self.rank, self.rank, dtype=dtype)
        self._records: dict[str, tuple[str, Tensor, Tensor]] = {}
        self._parents: dict[str, set[str]] = defaultdict(set)

    def add(self, transition_id: str, parent_id: str, key: Tensor, program: Tensor) -> None:
        if transition_id in self._records:
            raise KeyError(f"transition already exists: {transition_id}")
        key = key.detach().to(dtype=self.dtype).reshape(self.rank)
        program = program.detach().to(dtype=self.dtype).reshape(self.program_dim)
        self.V.add_(torch.outer(key, program))
        self.G.add_(torch.outer(key, key))
        self._records[transition_id] = (parent_id, key.clone(), program.clone())
        self._parents[parent_id].add(transition_id)

    def remove(self, transition_id: str) -> None:
        parent_id, key, program = self._records.pop(transition_id)
        self.V.sub_(torch.outer(key, program))
        self.G.sub_(torch.outer(key, key))
        self._parents[parent_id].remove(transition_id)
        if not self._parents[parent_id]:
            del self._parents[parent_id]

    def replace(self, transition_id: str, parent_id: str, key: Tensor, program: Tensor) -> None:
        self.remove(transition_id)
        self.add(transition_id, parent_id, key, program)

    def remove_parent(self, parent_id: str) -> None:
        for transition_id in sorted(self._parents.get(parent_id, set())):
            self.remove(transition_id)

    def read(self, query: Tensor) -> Tensor:
        query = query.to(dtype=self.dtype).reshape(self.rank)
        return query @ self.V

    @property
    def transition_ids(self) -> list[str]:
        return sorted(self._records)


def transition_field_algebra_validation(
    *, rank: int = 11, program_dim: int = 7, parent_count: int = 4, steps: int = 5, seed: int = 17
) -> dict[str, Any]:
    generator = torch.Generator().manual_seed(seed)
    entries = []
    for parent_index in range(parent_count):
        for step_index in range(steps):
            entries.append(
                (
                    f"t-{parent_index}-{step_index}",
                    f"p-{parent_index}",
                    torch.randn(rank, generator=generator, dtype=torch.float64),
                    torch.randn(program_dim, generator=generator, dtype=torch.float64),
                )
            )
    expected_v = sum(
        (torch.outer(key, program) for _, _, key, program in entries),
        torch.zeros(rank, program_dim, dtype=torch.float64),
    )
    expected_g = sum(
        (torch.outer(key, key) for _, _, key, _ in entries),
        torch.zeros(rank, rank, dtype=torch.float64),
    )
    query = torch.randn(rank, generator=generator, dtype=torch.float64)
    explicit_read = sum(
        ((query @ key) * program for _, _, key, program in entries),
        torch.zeros(program_dim, dtype=torch.float64),
    )

    field = TransitionAssociativeField(rank, program_dim)
    shuffled = entries[:]
    random.Random(seed).shuffle(shuffled)
    for transition_id, parent_id, key, program in shuffled:
        field.add(transition_id, parent_id, key, program)
    full_checks = {
        "V": torch.allclose(field.V, expected_v, atol=1.0e-10),
        "G": torch.allclose(field.G, expected_g, atol=1.0e-10),
        "read": torch.allclose(field.read(query), explicit_read, atol=1.0e-10),
    }

    original_v = field.V.clone()
    original_g = field.G.clone()
    transition_id, parent_id, key, program = entries[0]
    field.remove(transition_id)
    transition_removed = not torch.allclose(field.V, original_v)
    field.add(transition_id, parent_id, key, program)
    transition_restored = torch.allclose(field.V, original_v, atol=1.0e-10) and torch.allclose(
        field.G, original_g, atol=1.0e-10
    )

    parent_entries = [entry for entry in entries if entry[1] == "p-1"]
    parent_v = sum(
        (torch.outer(item[2], item[3]) for item in parent_entries),
        torch.zeros_like(field.V),
    )
    parent_g = sum(
        (torch.outer(item[2], item[2]) for item in parent_entries),
        torch.zeros_like(field.G),
    )
    field.remove_parent("p-1")
    parent_deleted = torch.allclose(field.V, original_v - parent_v, atol=1.0e-10) and torch.allclose(
        field.G, original_g - parent_g, atol=1.0e-10
    )
    for item in parent_entries:
        field.add(*item)
    parent_restored = torch.allclose(field.V, original_v, atol=1.0e-10) and torch.allclose(
        field.G, original_g, atol=1.0e-10
    )

    replace_id, replace_parent, replace_key, replace_program = entries[-1]
    new_key = torch.randn(rank, generator=generator, dtype=torch.float64)
    new_program = torch.randn(program_dim, generator=generator, dtype=torch.float64)
    field.replace(replace_id, replace_parent, new_key, new_program)
    replacement_expected_v = (
        original_v
        - torch.outer(replace_key, replace_program)
        + torch.outer(new_key, new_program)
    )
    replacement_expected_g = (
        original_g - torch.outer(replace_key, replace_key) + torch.outer(new_key, new_key)
    )
    replacement = torch.allclose(field.V, replacement_expected_v, atol=1.0e-10) and torch.allclose(
        field.G, replacement_expected_g, atol=1.0e-10
    )
    checks = {
        **full_checks,
        "transition_removed": transition_removed,
        "transition_restored": transition_restored,
        "parent_deleted": parent_deleted,
        "parent_restored": parent_restored,
        "parent_replacement": replacement,
        "arbitrary_insertion_order": full_checks["V"] and full_checks["G"],
    }
    return {
        "format": "decision_transition_field_algebra_validation_6a_v1",
        "rank": rank,
        "program_dim": program_dim,
        "transition_count": len(entries),
        "parent_count": parent_count,
        "checks": checks,
        "passed": all(checks.values()),
    }


def summarize_utility_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    scored = [row for row in rows if row.get("valid_for_loss")]
    utilities = [float(row["text_utility"]) for row in scored]
    categories = Counter(utility_category(value) for value in utilities)
    if not utilities:
        return {"count": 0, "category_counts": dict(categories)}
    ordered = sorted(utilities)

    def percentile(fraction: float) -> float:
        index = int(math.floor((len(ordered) - 1) * fraction + 0.5))
        return ordered[index]

    return {
        "count": len(utilities),
        "category_counts": dict(categories),
        "mean_std": mean_std(utilities),
        "min": min(utilities),
        "max": max(utilities),
        "percentiles": {
            "p01": percentile(0.01),
            "p05": percentile(0.05),
            "p25": percentile(0.25),
            "p50": percentile(0.50),
            "p75": percentile(0.75),
            "p95": percentile(0.95),
            "p99": percentile(0.99),
        },
    }


def program_geometry(latents: Tensor) -> dict[str, Any]:
    matrix = latents.detach().to(torch.float64).cpu()
    if matrix.ndim != 2 or matrix.shape[0] == 0:
        return {"count": 0}
    norms = matrix.norm(dim=1)
    normalized = matrix / norms.clamp_min(1.0e-12).unsqueeze(1)
    cosine = normalized @ normalized.T
    offdiag = cosine[~torch.eye(matrix.shape[0], dtype=torch.bool)]
    centered = matrix - matrix.mean(dim=0, keepdim=True)
    singular = torch.linalg.svdvals(centered)
    squared = singular.square()
    probabilities = squared / squared.sum().clamp_min(1.0e-12)
    effective_rank = float(torch.exp(-(probabilities * probabilities.clamp_min(1.0e-12).log()).sum()))
    return {
        "count": int(matrix.shape[0]),
        "dimension": int(matrix.shape[1]),
        "norm": mean_std(float(value) for value in norms.tolist()),
        "pairwise_cosine": mean_std(float(value) for value in offdiag.tolist()),
        "centered_effective_rank": effective_rank,
        "singular_values": [float(value) for value in singular.tolist()],
        "coordinate_variance": [float(value) for value in matrix.var(dim=0, unbiased=False).tolist()],
    }


def normalized_huber_reduction(model_huber: float, zero_huber: float) -> float:
    if zero_huber <= 0:
        return 0.0
    return 1.0 - float(model_huber) / float(zero_huber)


def transition_static_gate(
    *,
    summary: Mapping[str, Any],
    zero_summary: Mapping[str, Any],
    controls: Mapping[str, Mapping[str, Any]],
    task_results: Mapping[str, bool],
    geometry: Mapping[str, Any],
) -> dict[str, Any]:
    spearman_value = float(summary.get("u_text_vs_u_student_spearman") or -1.0)
    sign_value = float(summary.get("positive_negative_sign_agreement") or 0.0)
    huber = float(summary["sequence_utility_huber"]["mean"])
    zero_huber = float(zero_summary["sequence_utility_huber"]["mean"])
    reduction = normalized_huber_reduction(huber, zero_huber)
    control_hubers = {
        key: float(value["sequence_utility_huber"]["mean"])
        for key, value in controls.items()
        if value.get("sequence_utility_huber", {}).get("mean") is not None
    }
    checks = {
        "utility_spearman_gte_0_30": spearman_value >= 0.30,
        "sign_agreement_gte_0_65": sign_value >= 0.65,
        "huber_reduction_gte_0_30": reduction >= 0.30,
        "correct_better_than_all_controls": bool(control_hubers)
        and all(huber < value for value in control_hubers.values()),
        "positive_in_at_least_3_of_4_tasks": sum(bool(value) for value in task_results.values())
        >= 3,
        "noncollapsed_latent_geometry": float(
            geometry.get("centered_effective_rank") or 0.0
        )
        > 2.0,
    }
    return {
        "checks": checks,
        "utility_spearman": spearman_value,
        "sign_agreement": sign_value,
        "huber_reduction": reduction,
        "control_hubers": control_hubers,
        "positive_task_count": sum(bool(value) for value in task_results.values()),
        "passed": all(checks.values()),
    }


def granularity_advantage(
    transition: Mapping[str, Any], trajectory: Mapping[str, Any]
) -> dict[str, Any]:
    comparisons = {
        "utility_spearman": float(
            transition.get("utility_spearman", -1.0)
        )
        > float(trajectory.get("utility_spearman", -1.0)),
        "sign_agreement": float(transition.get("sign_agreement", 0.0))
        > float(trajectory.get("sign_agreement", 0.0)),
        "normalized_huber_reduction": float(
            transition.get("normalized_huber_reduction", 0.0)
        )
        > float(trajectory.get("normalized_huber_reduction", 0.0)),
        "swap_sensitivity": float(transition.get("swap_sensitivity", 0.0))
        > float(trajectory.get("swap_sensitivity", 0.0)),
        "heldout_task_consistency": int(
            transition.get("positive_task_count", 0)
        )
        > int(trajectory.get("positive_task_count", 0)),
    }
    return {
        "comparisons": comparisons,
        "advantage_count": sum(comparisons.values()),
        "passed": sum(comparisons.values()) >= 2,
    }
