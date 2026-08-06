from __future__ import annotations

import torch

from rcmf.injection.prefix import AdditiveTokenMemoryInjector
from rcmf.model.backends.mock import TinyCausalLM
from rcmf.training.stage_c1 import (
    StageC1ProgramField,
    augmented_queries_keys,
    explicit_field_read,
    select_teacher_conditions,
    sparse_bucket_kl,
    validate_program_field_algebra,
)


def test_select_teacher_conditions_uses_best_valid_memory_and_baseline_for_no_positive() -> None:
    memory_bank = [
        {"memory_id": "m0", "memory_index": 0, "task_id": "a"},
        {"memory_id": "m1", "memory_index": 1, "task_id": "b"},
    ]
    label_rows = [
        {
            "state_index": 0,
            "state_example_id": "s0",
            "task_id": "t0",
            "episode_id": "e0",
            "step_id": 1,
            "split": "train",
            "valid_mask": [True, True],
            "raw_utility": [0.02, 0.5],
            "source_pair_keys": ["e0:m0", "e0:m1"],
            "target_sha256_by_memory": ["target", "target"],
            "L0": 1.0,
        },
        {
            "state_index": 1,
            "state_example_id": "s1",
            "task_id": "t1",
            "episode_id": "e1",
            "step_id": 2,
            "split": "validation",
            "valid_mask": [True, True],
            "raw_utility": [-0.2, 0.005],
            "source_pair_keys": ["e1:m0", "e1:m1"],
            "target_sha256_by_memory": ["target2", "target2"],
            "L0": 2.0,
        },
        {
            "state_index": 2,
            "state_example_id": "s2",
            "task_id": "t2",
            "episode_id": "e2",
            "step_id": 3,
            "split": "train",
            "valid_mask": [False, False],
            "raw_utility": [None, None],
            "source_pair_keys": [None, None],
            "target_sha256_by_memory": [None, None],
            "L0": None,
        },
    ]
    teacher_rows = {
        "e0:m0": {"valid_for_loss": True, "leakage_overlap": [], "Lj_text": 0.98, "memory_text_sha256": "h0"},
        "e0:m1": {"valid_for_loss": True, "leakage_overlap": [], "Lj_text": 0.5, "memory_text_sha256": "h1"},
        "e1:m0": {"valid_for_loss": True, "leakage_overlap": [], "Lj_text": 2.2, "memory_text_sha256": "h0"},
        "e1:m1": {"valid_for_loss": True, "leakage_overlap": [], "Lj_text": 1.995, "memory_text_sha256": "h1"},
    }

    conditions = select_teacher_conditions(label_rows, memory_bank, teacher_rows)

    assert conditions[0]["condition"] == "positive_teacher"
    assert conditions[0]["best_memory_id"] == "m1"
    assert conditions[1]["condition"] == "baseline_teacher"
    assert conditions[1]["valid_for_stage_c"] is True
    assert conditions[2]["condition"] == "all_missing"
    assert conditions[2]["valid_for_stage_c"] is False


def test_augmented_queries_keys_exactly_include_prior() -> None:
    q = torch.tensor([[1.0, -2.0], [0.5, 0.25]])
    k = torch.tensor([[0.25, 3.0], [-1.0, 2.0], [4.0, 0.0]])
    mu = torch.tensor([0.1, -0.2, 0.3])
    temperature = torch.tensor(2.0)

    q_bar, k_bar = augmented_queries_keys(q, k, mu, temperature, rank=2)
    got = q_bar @ k_bar.T
    expected = mu.unsqueeze(0) + temperature * (q @ k.T) / (2**0.5)

    assert torch.allclose(got, expected)


def test_stage_c1_program_field_read_matches_explicit_sum_and_masks() -> None:
    torch.manual_seed(3)
    q_bar = torch.randn(4, 6)
    q_bar[:, -1] = 1.0
    k_bar = torch.randn(5, 6)
    programs = torch.randn(5, 7)
    gate = torch.sigmoid(torch.randn(4))
    mask = torch.tensor(
        [
            [True, True, False, True, True],
            [False, True, True, True, True],
            [True, True, True, True, True],
            [True, False, False, False, True],
        ]
    )
    field = StageC1ProgramField(memory_dim=8, rank=5, program_dim=7)

    got, _ = field.read(q_bar, k_bar, programs, gate, include_mask=mask)
    expected = explicit_field_read(q_bar, k_bar, programs, gate, include_mask=mask)

    assert torch.allclose(got, expected, atol=1.0e-7)


def test_stage_c1_program_field_algebra_and_reversibility_pass() -> None:
    report = validate_program_field_algebra(rank=8, program_dim=5, count=6, seed=7)

    assert report["passed"] is True
    assert report["full_read_max_abs_error"] <= 1.0e-9
    assert report["add_remove_v_norm"] <= 1.0e-10


def test_zero_initialized_additive_token_injector_keeps_embeddings_equal() -> None:
    model = TinyCausalLM(vocab_size=100, hidden_size=16)
    injector = AdditiveTokenMemoryInjector(program_dim=8, model_dim=16, num_tokens=4, position="last_user_k", initial_scale=0.0)
    with torch.no_grad():
        injector.prefix_scale.zero_()
        torch.nn.init.normal_(injector.mlp[-1].weight, mean=0.0, std=0.02)
    input_ids = torch.randint(1, 100, (2, 9))
    labels = torch.tensor([[-100, -100, -100, -100, -100, 1, 2, 3, 4], [-100, -100, -100, -100, 5, 6, 7, 8, 9]])
    base = model.get_input_embeddings()(input_ids)

    prepared = injector.prepare_train_inputs(
        model,
        input_ids=input_ids,
        attention_mask=torch.ones_like(input_ids),
        labels=labels,
        memory_z=torch.randn(2, 8),
        injection_token_indices=torch.tensor([[0, 1, 2], [0, 1, 2]]),
    )

    assert torch.allclose(prepared.inputs["inputs_embeds"], base)


def test_signed_dot_has_gradients_even_when_largest_coordinates_are_disjoint() -> None:
    q = torch.tensor([[10.0, 0.0, 0.0, 0.0]], requires_grad=True)
    k = torch.tensor([[0.0, 10.0, 0.0, 0.0]], requires_grad=True)
    score = q @ k.T
    loss = score.sum()
    loss.backward()

    assert float(score.item()) == 0.0
    assert q.grad is not None and float(q.grad.abs().sum()) > 0.0
    assert k.grad is not None and float(k.grad.abs().sum()) > 0.0


def test_sparse_bucket_kl_is_zero_for_identical_distribution() -> None:
    teacher_log_probs = torch.log(torch.tensor([[0.2, 0.3, 0.1]], dtype=torch.float32))
    student_log_probs = teacher_log_probs.clone()
    other = torch.tensor([0.4])

    kl = sparse_bucket_kl(student_log_probs, torch.log(other), teacher_log_probs, other)

    assert torch.allclose(kl, torch.zeros_like(kl), atol=1.0e-7)
