from __future__ import annotations

import copy
import inspect
from pathlib import Path

import pytest

from scripts.run_rcmf_joint_full_bank_live_9a import build_live_manifest

from rcmf.benchmarks.appworld.scoreable_count_contract_14l import (
    EXACT_REPRODUCTION,
    SEALED_UPSTREAM_OUTCOMES,
    scoreable_count_contract,
    validate_scoreable_population,
)


def _fixture(train_count: int, heldout_count: int) -> dict[str, object]:
    train_tasks = [f"train-{index:02d}" for index in range(29)]
    heldout_tasks = [f"heldout-{index:02d}" for index in range(8)]
    rows = []
    total = train_count + heldout_count
    labels = ("POSITIVE", "NEUTRAL", "HARMFUL")
    for index in range(total):
        is_train = index < train_count
        state_id = f"state-{index:04d}"
        tasks = train_tasks if is_train else heldout_tasks
        rows.append(
            {
                "state_example_id": state_id,
                "state_task_id": tasks[index % len(tasks)],
                "state_step_id": index,
                "model_split": (
                    "model_train" if is_train else "heldout_train_validation"
                ),
                "label": labels[index % len(labels)],
                "bare_condition_key": f"{state_id}:bare",
                "raw_condition_key": f"{state_id}:raw",
                "bare_prompt_sha256": "a" * 64,
                "raw_prompt_sha256": "b" * 64,
            }
        )
    label_counts = {label: 0 for label in labels}
    for row in rows:
        label_counts[str(row["label"])] += 1
    outcomes_sha = "c" * 64
    teacher_sha = "d" * 64
    ids = [str(row["state_example_id"]) for row in rows]
    outcomes = {
        "rows": rows,
        "state_count": total,
        "condition_count": 2 * total,
        "label_counts": label_counts,
        "replay_semantic_missing_rows": [],
        "replay_semantic_missing_count": 0,
        "over_context_missing_count": 0,
        "minimum_label_gate_passed": True,
        "maximum_state_space_exhausted": False,
    }
    teacher_cache = {
        "ordered_state_ids": ids,
        "policy_rows": {state_id: {} for state_id in ids},
        "teacher_rows": {state_id: {} for state_id in ids},
        "paired_outcomes_sha256": outcomes_sha,
    }
    teacher_report = {
        "passed": True,
        "state_count": total,
        "bare_policy_count": total,
        "raw_policy_count": total,
        "cache_sha256": teacher_sha,
    }
    selections = [
        {"state_example_id": state_id, "scoreable": True, "over_context": False}
        for state_id in ids
    ]
    return {
        "outcomes": outcomes,
        "teacher_cache": teacher_cache,
        "teacher_report": teacher_report,
        "selections": selections,
        "train_tasks": train_tasks,
        "heldout_tasks": heldout_tasks,
        "outcomes_sha": outcomes_sha,
        "teacher_sha": teacher_sha,
    }


def _validate(contract: dict[str, object], fixture: dict[str, object]) -> dict[str, object]:
    return validate_scoreable_population(
        contract=contract,
        outcomes=fixture["outcomes"],
        teacher_cache=fixture["teacher_cache"],
        teacher_report=fixture["teacher_report"],
        selection_rows=fixture["selections"],
        train_task_ids=fixture["train_tasks"],
        heldout_task_ids=fixture["heldout_tasks"],
        minimum_per_label=40,
        maximum_state_count=499,
        outcomes_sha256=str(fixture["outcomes_sha"]),
        teacher_cache_sha256=str(fixture["teacher_sha"]),
    )


def test_three_demo_exact_366_98_passes_and_deviation_fails() -> None:
    contract = scoreable_count_contract(
        arm_id="3d",
        policy=EXACT_REPRODUCTION,
        expected_train=366,
        expected_heldout=98,
    )
    result = _validate(contract, _fixture(366, 98))
    assert result["passed"]
    with pytest.raises(ValueError, match="exact_train_paired_count"):
        _validate(contract, _fixture(365, 99))


@pytest.mark.parametrize("train_count,heldout_count", [(80, 40), (100, 50), (324, 83)])
def test_one_demo_dynamic_policy_accepts_multiple_valid_populations(
    train_count: int, heldout_count: int
) -> None:
    contract = scoreable_count_contract(
        arm_id="1d", policy=SEALED_UPSTREAM_OUTCOMES
    )
    result = _validate(contract, _fixture(train_count, heldout_count))
    assert result["derived_counts"]["model_train"] == train_count
    assert result["derived_counts"]["heldout_train_validation"] == heldout_count
    assert "expected_train_paired_states" not in contract
    assert "expected_heldout_paired_states" not in contract


def test_dynamic_policy_rejects_exact_count_fields() -> None:
    fixture = _fixture(80, 40)
    contract = scoreable_count_contract(
        arm_id="1d", policy=SEALED_UPSTREAM_OUTCOMES
    )
    contract["expected_train_paired_states"] = 80
    with pytest.raises(ValueError, match="dynamic_policy_has_no_exact_counts"):
        _validate(contract, fixture)


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("duplicate", "paired_state_ids_unique"),
        ("malformed", "complete_bare_raw_pairs"),
        ("empty_heldout", "heldout_population_nonempty"),
        ("bad_split", "all_rows_in_fixed_task_split"),
        ("teacher_ids", "teacher_order_exact"),
        ("bad_hash", "teacher_cache_binds_paired_outcomes"),
        ("panel", "panel_flags_consistent"),
        ("false_exhaustion", "maximum_state_space_flag_consistent"),
        ("duplicate_missing", "replay_missing_ids_unique"),
        ("overlapping_missing", "missing_types_disjoint"),
    ],
)
def test_dynamic_policy_fails_closed_on_invalid_population(
    mutation: str, match: str
) -> None:
    fixture = _fixture(80, 40)
    if mutation == "duplicate":
        fixture["outcomes"]["rows"][-1]["state_example_id"] = "state-0000"
    elif mutation == "malformed":
        fixture["outcomes"]["rows"][0].pop("raw_condition_key")
    elif mutation == "empty_heldout":
        rows = fixture["outcomes"]["rows"]
        fixture["outcomes"]["rows"] = rows[:80]
        fixture["outcomes"]["state_count"] = 80
        fixture["outcomes"]["condition_count"] = 160
        fixture["teacher_cache"]["ordered_state_ids"] = fixture["teacher_cache"]["ordered_state_ids"][:80]
        fixture["teacher_cache"]["policy_rows"] = dict(list(fixture["teacher_cache"]["policy_rows"].items())[:80])
        fixture["teacher_cache"]["teacher_rows"] = dict(list(fixture["teacher_cache"]["teacher_rows"].items())[:80])
        fixture["teacher_report"]["state_count"] = 80
        fixture["teacher_report"]["bare_policy_count"] = 80
        fixture["teacher_report"]["raw_policy_count"] = 80
        fixture["selections"] = fixture["selections"][:80]
        counts = {"POSITIVE": 0, "NEUTRAL": 0, "HARMFUL": 0}
        for row in fixture["outcomes"]["rows"]:
            counts[row["label"]] += 1
        fixture["outcomes"]["label_counts"] = counts
        fixture["outcomes"]["minimum_label_gate_passed"] = False
        fixture["outcomes"]["maximum_state_space_exhausted"] = True
    elif mutation == "bad_split":
        fixture["outcomes"]["rows"][0]["state_task_id"] = "unknown-task"
    elif mutation == "teacher_ids":
        fixture["teacher_cache"]["ordered_state_ids"] = list(
            reversed(fixture["teacher_cache"]["ordered_state_ids"])
        )
    elif mutation == "bad_hash":
        fixture["teacher_cache"]["paired_outcomes_sha256"] = "e" * 64
    elif mutation == "panel":
        fixture["outcomes"]["minimum_label_gate_passed"] = False
        fixture["outcomes"]["maximum_state_space_exhausted"] = False
    elif mutation == "false_exhaustion":
        fixture["outcomes"]["maximum_state_space_exhausted"] = True
    elif mutation == "duplicate_missing":
        row = {
            "state_example_id": "missing-state",
            "state_task_id": fixture["train_tasks"][0],
        }
        fixture["outcomes"]["replay_semantic_missing_rows"] = [row, dict(row)]
        fixture["outcomes"]["replay_semantic_missing_count"] = 2
    elif mutation == "overlapping_missing":
        state_id = fixture["selections"][-1]["state_example_id"]
        fixture["selections"][-1]["scoreable"] = False
        fixture["outcomes"]["rows"] = fixture["outcomes"]["rows"][:-1]
        fixture["outcomes"]["state_count"] -= 1
        fixture["outcomes"]["condition_count"] -= 2
        fixture["outcomes"]["label_counts"]["HARMFUL"] -= 1
        fixture["outcomes"]["replay_semantic_missing_rows"] = [
            {
                "state_example_id": state_id,
                "state_task_id": fixture["heldout_tasks"][-1],
            }
        ]
        fixture["outcomes"]["replay_semantic_missing_count"] = 1
        fixture["teacher_cache"]["ordered_state_ids"] = (
            fixture["teacher_cache"]["ordered_state_ids"][:-1]
        )
        fixture["teacher_cache"]["policy_rows"].pop(state_id)
        fixture["teacher_cache"]["teacher_rows"].pop(state_id)
        fixture["teacher_report"]["state_count"] -= 1
        fixture["teacher_report"]["bare_policy_count"] -= 1
        fixture["teacher_report"]["raw_policy_count"] -= 1
    contract = scoreable_count_contract(
        arm_id="1d", policy=SEALED_UPSTREAM_OUTCOMES
    )
    with pytest.raises(ValueError, match=match):
        _validate(contract, fixture)


def test_unknown_or_missing_policy_fails_closed() -> None:
    with pytest.raises(ValueError, match="Unknown scoreable count policy"):
        scoreable_count_contract(arm_id="1d", policy="")
    with pytest.raises(ValueError, match="Unknown scoreable count policy"):
        scoreable_count_contract(arm_id="1d", policy="dynamic-ish")


def test_live_manifest_derives_conditions_from_dynamic_heldout_population() -> None:
    fixture = _fixture(80, 40)
    heldout_rows = [
        row
        for row in fixture["outcomes"]["rows"]
        if row["model_split"] == "heldout_train_validation"
    ]
    state_shuffle = {
        row["state_example_id"]: heldout_rows[(index + 1) % len(heldout_rows)][
            "state_example_id"
        ]
        for index, row in enumerate(heldout_rows)
    }
    manifest = build_live_manifest(
        outcomes=fixture["outcomes"]["rows"],
        state_shuffle=state_shuffle,
    )
    assert manifest["state_count_per_epoch"] == 40
    assert manifest["condition_count"] == 2 * 40 * 4
    assert len(manifest["conditions"]) == 320


def test_live_manifest_rejects_empty_heldout_population() -> None:
    fixture = _fixture(80, 40)
    train_rows = [
        row for row in fixture["outcomes"]["rows"] if row["model_split"] == "model_train"
    ]
    with pytest.raises(ValueError, match="nonempty heldout"):
        build_live_manifest(outcomes=train_rows, state_shuffle={})

def test_smoke_uses_canonical_static_counts_path_key(tmp_path: Path) -> None:
    from scripts.run_rcmf_joint_full_bank_9a import _paths, _smoke

    paths = _paths(
        {
            "parent_exp025b": str(tmp_path / "b"),
            "parent_exp028a": str(tmp_path / "a"),
        },
        tmp_path / "artifacts",
    )
    assert "static_counts" in paths
    assert "runtime_counts" not in paths
    assert '_json(paths["static_counts"])' in inspect.getsource(_smoke)
