from __future__ import annotations

import math

import pytest
import torch

from rcmf.training.oracle_capacity_5e import (
    FreeMemoryLatentTable,
    FreePairLatentTable,
    perturbation_ratios,
    scatter_token_delta,
    select_balanced_validation_subset,
    select_last_user_k_indices,
    validate_target_token_utility_identity,
)


def _pair_row(index: int, category: str, memory: int) -> dict:
    base = math.log(0.4)
    teacher = math.log(0.5)
    return {
        "format": "stage_c_pair_response_cache_5d_v1",
        "pair_id": f"s{index}::memory::m{memory}",
        "pair_key": f"e{index}:m{memory}",
        "state_example_id": f"s{index}",
        "memory_id": f"m{memory}",
        "memory_stage_index": memory,
        "split": "validation",
        "selection_category": category,
        "text_utility": -base + teacher,
        "target_positions": [
            {
                "target_token_id": 1,
                "baseline_target_logprob": base,
                "teacher_target_logprob": teacher,
            }
        ],
    }


def test_target_token_teacher_delta_equals_text_utility() -> None:
    row = _pair_row(0, "positive", 0)

    report = validate_target_token_utility_identity([row])

    assert report["passed"] is True
    assert report["max_abs_error"] < 1.0e-8


def test_target_token_identity_rejects_mismatched_utility() -> None:
    row = _pair_row(0, "positive", 0)
    row["text_utility"] = 10.0

    report = validate_target_token_utility_identity([row], atol=1.0e-6)

    assert report["passed"] is False
    assert report["error_count"] == 1


def test_select_last_user_k_uses_only_prompt_last_user_tokens() -> None:
    selected = select_last_user_k_indices(
        input_len=10,
        last_user_token_indices=[2, 3, 4, 8, 9],
        labels=[-100, -100, -100, -100, -100, 5, 6, 7, 8, 9],
        k=3,
    )

    assert selected == [2, 3, 4]


def test_direct_delta_scatter_touches_only_selected_tokens() -> None:
    base = torch.zeros(1, 6, 3)
    selected = torch.tensor([[1, 4, -1]])
    delta = torch.tensor([[[1.0, 0.0, 0.0], [0.0, 2.0, 0.0], [9.0, 9.0, 9.0]]])

    scattered = scatter_token_delta(base_embeddings=base, selected_indices=selected, delta_slots=delta)

    assert torch.allclose(scattered[0, 1], torch.tensor([1.0, 0.0, 0.0]))
    assert torch.allclose(scattered[0, 4], torch.tensor([0.0, 2.0, 0.0]))
    assert float(scattered[0, [0, 2, 3, 5]].abs().sum()) == 0.0


def test_zero_delta_has_zero_perturbation_ratio() -> None:
    delta = torch.zeros(2, 4, 3)
    base = torch.ones(2, 4, 3)

    ratios = perturbation_ratios(delta_slots=delta, selected_base_embeddings=base)

    assert torch.allclose(ratios, torch.zeros(2))


def test_pair_latent_lookup_uses_pair_identity_not_memory_identity() -> None:
    table = FreePairLatentTable(["s0::memory::m0", "s1::memory::m0"], 2)
    with torch.no_grad():
        table.latents[0] = torch.tensor([1.0, 0.0])
        table.latents[1] = torch.tensor([0.0, 2.0])

    z = table([
        {"pair_id": "s1::memory::m0", "memory_stage_index": 0},
        {"pair_id": "s0::memory::m0", "memory_stage_index": 0},
    ])

    assert torch.allclose(z, torch.tensor([[0.0, 2.0], [1.0, 0.0]]))


def test_memory_latent_lookup_uses_memory_identity_only() -> None:
    table = FreeMemoryLatentTable([0, 1], 2)
    with torch.no_grad():
        table.latents[table.memory_to_index[0]] = torch.tensor([1.0, 0.0])
        table.latents[table.memory_to_index[1]] = torch.tensor([0.0, 2.0])

    z = table([
        {"pair_id": "s9::memory::m1", "memory_stage_index": 1},
        {"pair_id": "s8::memory::m1", "memory_stage_index": 1},
        {"pair_id": "s7::memory::m0", "memory_stage_index": 0},
    ])

    assert torch.allclose(z, torch.tensor([[0.0, 2.0], [0.0, 2.0], [1.0, 0.0]]))


def test_balanced_subset_spreads_across_memories() -> None:
    rows = []
    categories = ["positive", "neutral", "negative", "random"]
    for memory in range(4):
        for category in categories:
            for offset in range(2):
                rows.append(_pair_row(index=100 * memory + 10 * categories.index(category) + offset, category=category, memory=memory))

    selected, report = select_balanced_validation_subset(rows, target_total=16, seed=5)

    assert len(selected) == 16
    assert report["balanced"] is True
    assert report["selected_by_category"] == {category: 4 for category in categories}
    assert report["unique_memory_count"] == 4


def test_no_selector_payload_is_needed_by_oracle_helpers() -> None:
    table = FreePairLatentTable(["pair"], 2)

    with pytest.raises(KeyError):
        table([{"pair_id": "missing", "selector_payload": {"score": 1.0}}])
