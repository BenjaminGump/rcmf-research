from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from rcmf.training.state_conditioned_transition_6b import (
    deterministic_parent_split,
)


SELECTOR_PARENT_SPLIT_SEED = 18018
SELECTOR_TRAIN_PARENT_COUNT = 29
SELECTOR_HELDOUT_PARENT_COUNT = 8
CAUSAL_PANEL_INITIAL_STATE_COUNT = 256
CAUSAL_PANEL_MAXIMUM_STATE_COUNT = 499
CAUSAL_PANEL_MINIMUM_PER_LABEL = 40


def reconstruct_historical_selector_parent_split(
    transitions: Sequence[Mapping[str, Any]],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild the 7b selector split from authoritative transition parents."""

    required = {
        "algorithm": "sha256_order_first_heldout_then_remaining_train",
        "seed": SELECTOR_PARENT_SPLIT_SEED,
        "train_parent_count": SELECTOR_TRAIN_PARENT_COUNT,
        "heldout_parent_count": SELECTOR_HELDOUT_PARENT_COUNT,
    }
    actual = {key: contract.get(key) for key in required}
    if actual != required:
        raise ValueError(
            f"Selector parent-split contract differs: {actual} != {required}"
        )

    generated = deterministic_parent_split(
        transitions,
        seed=int(contract["seed"]),
        train_parent_count=int(contract["train_parent_count"]),
        heldout_parent_count=int(contract["heldout_parent_count"]),
    )
    task_by_parent: dict[str, str] = {}
    for row in transitions:
        parent_id = str(row["parent_memory_id"])
        task_id = str(row["parent_task_id"])
        prior = task_by_parent.setdefault(parent_id, task_id)
        if prior != task_id:
            raise ValueError(f"Parent maps to multiple tasks: {parent_id}")
    split_by_parent = {
        str(parent_id): str(split)
        for parent_id, split in generated["split_by_parent"].items()
    }
    split_by_parent_task: dict[str, str] = {}
    for parent_id, split in split_by_parent.items():
        task_id = task_by_parent[parent_id]
        prior = split_by_parent_task.setdefault(task_id, split)
        if prior != split:
            raise ValueError(f"Task maps to multiple parent splits: {task_id}")
    counts = Counter(split_by_parent.values())
    expected_counts = Counter(
        {
            "train": SELECTOR_TRAIN_PARENT_COUNT,
            "heldout": SELECTOR_HELDOUT_PARENT_COUNT,
        }
    )
    if counts != expected_counts:
        raise ValueError(f"Selector parent counts differ: {counts}")
    return {
        "format": "identity_reconciled_locked_parent_split_7b_v1",
        "split_by_parent": dict(sorted(split_by_parent.items())),
        "split_by_parent_task": dict(sorted(split_by_parent_task.items())),
        "train_parent_count": SELECTOR_TRAIN_PARENT_COUNT,
        "heldout_parent_count": SELECTOR_HELDOUT_PARENT_COUNT,
    }


def resolved_causal_panel_contract(
    pipeline: Mapping[str, Any],
) -> dict[str, int]:
    configured = pipeline["reproduction_contract"]["causal_panel"]
    expected = {
        "initial_state_count": CAUSAL_PANEL_INITIAL_STATE_COUNT,
        "maximum_state_count": CAUSAL_PANEL_MAXIMUM_STATE_COUNT,
        "minimum_per_label": CAUSAL_PANEL_MINIMUM_PER_LABEL,
    }
    actual = {key: int(configured[key]) for key in expected}
    if actual != expected:
        raise ValueError(f"Causal-panel contract differs: {actual} != {expected}")
    return actual


def validate_post_d06_expectations_are_not_panel_inputs(
    pipeline: Mapping[str, Any], panel: Mapping[str, Any]
) -> dict[str, Any]:
    expected = pipeline["expected"]
    downstream_total = int(expected["downstream_train_states"]) + int(
        expected["downstream_heldout_states"]
    )
    checks = {
        "panel_initial_is_historical_contract": int(panel["initial_state_count"])
        == CAUSAL_PANEL_INITIAL_STATE_COUNT,
        "panel_maximum_is_historical_contract": int(panel["maximum_state_count"])
        == CAUSAL_PANEL_MAXIMUM_STATE_COUNT,
        "panel_minimum_is_historical_contract": int(panel["minimum_per_label"])
        == CAUSAL_PANEL_MINIMUM_PER_LABEL,
        "downstream_total_not_used_as_initial": int(panel["initial_state_count"])
        != downstream_total,
        "downstream_total_not_used_as_maximum": int(panel["maximum_state_count"])
        != downstream_total,
    }
    return {
        "format": "exp037a_reproduction_contract_independence_14e_v1",
        "checks": checks,
        "passed": all(checks.values()),
        "post_d06_expected_completed": {
            "model_train": int(expected["downstream_train_states"]),
            "heldout": int(expected["downstream_heldout_states"]),
        },
        "panel": {key: int(value) for key, value in panel.items()},
    }
