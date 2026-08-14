from __future__ import annotations

import torch

from rcmf.training.oracle_decoder_5fc import LinearDeltaDecoder
from scripts.run_transition_behavior_6a import (
    _adapt_response_rows,
    _project_latent_copy,
    _teacher_validity_gate,
)


def test_response_rows_are_adapted_without_adding_student_memory_text() -> None:
    source = {
        "pair_id": "state::transition::one",
        "entity_id": "transition-one",
        "entity_task_id": "source-task",
        "teacher_prompt_tokens": 140,
        "prompt_tokens": 100,
    }
    adapted = _adapt_response_rows([source], identity_ids=["transition-one"])
    assert len(adapted) == 1
    assert adapted[0]["memory_id"] == "transition-one"
    assert adapted[0]["memory_stage_index"] == 0
    assert adapted[0]["raw_memory_tokens"] == 40
    assert "raw_memory_text" not in adapted[0]


def test_teacher_validity_gate_requires_both_utility_signs() -> None:
    summary = {
        "validation": {
            "passed": True,
            "scoreable_pair_count": 40,
            "error_count": 0,
        },
        "reproducibility": {"passed": True},
        "representative_inspection": {"passed": True},
        "teacher_analysis": {
            "utility": {
                "category_counts": {"positive": 24, "neutral": 0, "negative": 16}
            },
            "correlations": {"action_tokens": 0.1, "observation_tokens": -0.2},
            "target_exact_substring_count": 10,
        },
    }
    rows = [
        {
            "valid_for_loss": True,
            "text_utility": 0.2 if index < 24 else -0.2,
            "normalized_target_exact_substring_in_transition": index % 4 == 0,
        }
        for index in range(40)
    ]
    assert _teacher_validity_gate(summary, rows)["passed"]
    summary["teacher_analysis"]["utility"]["category_counts"]["negative"] = 0
    assert not _teacher_validity_gate(summary, rows)["passed"]


def test_latent_copy_is_projected_for_receiving_identity_budget() -> None:
    decoder = LinearDeltaDecoder(2, 2)
    decoder.initialize_from_basis(torch.eye(2))
    latents = torch.tensor([[4.0, 0.0], [0.0, 2.0]])
    base_norms = torch.tensor([1.0, 0.5])
    projected = _project_latent_copy(
        decoder=decoder, latents=latents, base_norms=base_norms
    )
    ratios = decoder(projected).norm(dim=1) / base_norms
    assert torch.all(ratios <= 1.00001)
    assert torch.equal(latents, torch.tensor([[4.0, 0.0], [0.0, 2.0]]))
