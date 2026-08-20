from __future__ import annotations

import builtins

import torch

from rcmf.config import load_config
from rcmf.benchmarks.program_adapter import ProgramBenchmarkAdapter
from rcmf.training.state_conditioned_program_7d import (
    WeightedFactorizedTransitionField,
    assert_program_student_contract,
)
from rcmf.training.state_conditioned_program_fast_7df import (
    FactorizedProgramFast,
    FreeIDProgramFast,
    build_bounded_a_pairs,
    decoded_effect_stability,
    fast_field_validation,
    select_transition_program_inputs,
    transition_boundary_invariance,
)
from scripts.prepare_state_conditioned_program_fast_7df import _runtime_projection
from scripts.run_state_conditioned_program_fast_7df import (
    _behavioral_objective,
    _decoder_evaluation_delta,
    _fallback_decoder_indices,
    _prefix_equivalence_or_resume,
)
from rcmf.utils.serialization import atomic_write_json


def test_fast_config_pins_exact_selector_file_hash() -> None:
    cfg = load_config(
        "configs/benchmark/stage_c_state_conditioned_program_fast_7df.yaml"
    )
    value = str(cfg.raw["stage_c_7df"]["expected_selector_ensemble_sha256"])
    assert len(value) == 64
    assert value == (
        "c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f"
    )


def test_runtime_projection_counts_bare_state_forwards() -> None:
    cfg = load_config(
        "configs/benchmark/stage_c_state_conditioned_program_fast_7df.yaml"
    )
    runtime = _runtime_projection(
        settings=cfg.raw["stage_c_7df"],
        unique_pairs=224,
        unique_states=189,
        new_teacher_rows=224,
        new_clean_decoder_rows=1,
    )
    expected = runtime["scenarios"]["expected"]
    assert expected["bare_baseline_forward_count"] == 189
    assert expected["teacher_forced_control_count"] == 8
    assert expected["qwen_forward_count"] == 2017


def test_pair_objective_keeps_target_delta_and_sparse_teacher_terms() -> None:
    cfg = load_config(
        "configs/benchmark/stage_c_state_conditioned_program_fast_7df.yaml"
    )
    objective = _behavioral_objective(cfg.raw["stage_c_7df"], "pair_latents")
    assert objective.sequence_utility_weight == 1.0
    assert objective.sparse_teacher_kl_weight == 0.05
    assert objective.target_delta_weight == 0.10


def test_decoder_heldout_delta_moves_to_the_evaluation_device() -> None:
    values = torch.zeros(3, 4 * 5)
    result = _decoder_evaluation_delta(
        values, device=torch.device("meta"), model_dim=5
    )
    assert result.shape == (3, 4, 5)
    assert result.device.type == "meta"


def test_completed_prefix_equivalence_is_resumed_without_backend_call(tmp_path) -> None:
    rows = [
        {"pair_id": f"pair-{index}", "input_ids": list(range(length))}
        for index, length in enumerate((3, 5, 7, 9))
    ]
    atomic_write_json(
        tmp_path / "prefix_cache_equivalence.json",
        {
            "format": "prefix_kv_equivalence_7df_v1",
            "representative_pair_count": 4,
            "reports": [
                {"pair_id": "pair-0"},
                {"pair_id": "pair-2"},
                {"pair_id": "pair-2"},
                {"pair_id": "pair-3"},
            ],
            "passed": False,
            "selected_training_path": "full_forward",
        },
    )
    resumed = _prefix_equivalence_or_resume(
        backend=None,
        rows=rows,
        device=torch.device("cpu"),
        settings={},
        artifact_dir=tmp_path,
    )
    assert resumed["resumed_from_completed_equivalence"]
    assert resumed["selected_training_path"] == "full_forward"


def test_fallback_decoder_split_is_exact_and_grouped() -> None:
    pair_ids = [
        f"state-{state}::memory::memory-{memory}"
        for state in range(40)
        for memory in range(5)
    ]
    calibration, heldout, report = _fallback_decoder_indices(pair_ids)
    assert len(calibration) == 64
    assert len(heldout) == 16
    assert not set(calibration) & set(heldout)
    assert report["state_overlap"] == []
    assert report["passed"]


def test_decoded_effect_stability_uses_latents_and_decoder_weight() -> None:
    first = torch.tensor([[1.0, 0.0], [0.0, 1.0]])
    second = torch.tensor([[0.99, 0.01], [0.01, 0.99]])
    decoder_weight = torch.eye(2)
    report = decoded_effect_stability(
        first,
        second,
        decoder_weight,
        [0.2, -0.3],
        [0.19, -0.29],
    )

    assert report["decoded_delta_cosine_mean"] > 0.99
    assert report["repeat_utility_spearman"] > 0.999
    assert report["repeat_sign_agreement"] == 1.0
    assert report["passed"]


def test_free_id_known_rows_receive_gradients_and_unknown_rows_stay_zero() -> None:
    model = FreeIDProgramFast(["known-a", "known-b"], program_dim=4)
    output = model.forward_ids(
        ["known-a", "unknown", "known-b"], device=torch.device("cpu")
    )
    output.sum().backward()

    assert model.rows.weight.grad is not None
    assert torch.equal(output[1], torch.zeros(4))
    assert torch.equal(model.rows.weight.grad, torch.ones_like(model.rows.weight.grad))


TRANSITION_VIEWS = (
    "source_task_goal",
    "pre_action_state",
    "complete_action",
    "post_action_observation",
    "full_transition_global",
)


def _program(*, include_outcome: bool) -> FactorizedProgramFast:
    torch.manual_seed(11)
    return FactorizedProgramFast(
        state_vector_count=4,
        transition_view_names=TRANSITION_VIEWS,
        representation_dim=3,
        controller_rank=2,
        program_dim=5,
        hidden_dim=7,
        dropout=0.0,
        include_outcome=include_outcome,
    )


def test_primary_program_excludes_observation_and_records_exact_six_views() -> None:
    state = torch.randn(3, 4, 3)
    transition = torch.randn(3, 10, 3)
    primary = _program(include_outcome=False)
    report = transition_boundary_invariance(
        primary,
        state_views=state,
        transition_views=transition,
        observation_permutation=torch.tensor([1, 2, 0]),
    )
    provenance = primary.input_provenance()

    assert report["static_program_unchanged"]
    assert report["conditional_basis_unchanged"]
    assert report["pair_latent_unchanged"]
    assert provenance["selected_vector_count"] == 6
    assert [row["view"] for row in provenance["selected_views"]] == [
        "source_task_goal",
        "source_task_goal",
        "pre_action_state",
        "pre_action_state",
        "complete_action",
        "complete_action",
    ]
    assert not provenance["post_action_observation_accessed"]
    assert not provenance["full_transition_global_accessed"]


def test_action_changes_primary_while_action_plus_outcome_can_use_observation() -> None:
    state = torch.randn(2, 4, 3)
    transition = torch.randn(2, 10, 3)
    primary = _program(include_outcome=False).eval()
    diagnostic = _program(include_outcome=True).eval()

    changed_action = transition.clone()
    changed_action[:, 4:6] += 2.0
    shuffled_observation = transition.clone()
    shuffled_observation[:, 6:10] = transition.flip(0)[:, 6:10]

    with torch.no_grad():
        original_primary = primary.components(state, transition)
        action_primary = primary.components(state, changed_action)
        original_diagnostic = diagnostic(state, transition)
        observation_diagnostic = diagnostic(state, shuffled_observation)

    assert not torch.equal(original_primary["static"], action_primary["static"])
    assert not torch.equal(original_primary["basis"], action_primary["basis"])
    assert not torch.equal(original_diagnostic, observation_diagnostic)


def test_program_student_prompt_contract_has_no_raw_transition() -> None:
    report = assert_program_student_contract(
        [
            {
                "pair_id": "s::transition::t",
                "state_representation_id": "s",
                "transition_representation_id": "t",
                "latent_provenance": _program(include_outcome=False).input_provenance(),
            }
        ]
    )
    assert report["passed"]
    assert not report["student_prompt_contains_raw_transition"]


def test_fast_field_never_calls_audit_rebuild_or_scans_unrelated_records() -> None:
    field = WeightedFactorizedTransitionField(3, 2, 4)

    def forbidden_rebuild() -> None:
        raise AssertionError("fast operation called audit_rebuild")

    field.audit_rebuild = forbidden_rebuild  # type: ignore[method-assign]
    for index, parent in enumerate(("p0", "p0", "p1")):
        field.add_fast(
            f"t{index}",
            parent,
            0.5 if parent == "p0" else 1.0,
            torch.full((3,), float(index + 1)),
            torch.full((4,), float(index + 1)),
            torch.full((2, 4), float(index + 1)),
        )

    class NoEnumerationDict(dict[str, object]):
        def items(self):  # type: ignore[override]
            raise AssertionError("unrelated records were enumerated")

        def __iter__(self):
            raise AssertionError("unrelated records were iterated")

    field.records = NoEnumerationDict(field.records)  # type: ignore[assignment]
    assert field.remove_parent_fast("p0") == ["t0", "t1"]
    assert set(field.records.keys()) == {"t2"}


def test_fast_field_matches_explicit_and_audit_paths() -> None:
    report = fast_field_validation(seed=17)
    assert report["passed"]
    assert all(report["checks"].values())


def test_primary_and_diagnostic_transition_vector_counts() -> None:
    values = torch.randn(2, 10, 4)
    primary, primary_provenance = select_transition_program_inputs(
        values, view_names=TRANSITION_VIEWS, include_outcome=False
    )
    diagnostic, diagnostic_provenance = select_transition_program_inputs(
        values, view_names=TRANSITION_VIEWS, include_outcome=True
    )
    assert primary.shape == (2, 6, 4)
    assert diagnostic.shape == (2, 10, 4)
    assert primary_provenance["selected_vector_count"] == 6
    assert diagnostic_provenance["selected_vector_count"] == 10


def test_bounded_a_manifest_is_exact_and_covers_all_train_tasks() -> None:
    transitions = [f"t{index}" for index in range(5)]
    states = [f"s{index}" for index in range(37)]
    labels = []
    utilities = {}
    for state_index, state_id in enumerate(states):
        for transition_index, transition_id in enumerate(transitions):
            labels.append(
                {
                    "state_example_id": state_id,
                    "state_task_id": f"task{state_index}",
                    "transition_id": transition_id,
                    "transition_parent_id": f"parent{transition_index}",
                    "transition_parent_task_id": f"source{transition_index}",
                    "signature_class_id": f"class{transition_index}",
                    "procedural_tier": transition_index,
                    "exact_api_sequence": transition_index >= 3,
                    "state_stage_conflict_count": int(transition_index == 0),
                }
            )
            utilities[(state_id, transition_id)] = float(transition_index - 2) / 10.0
    scores = torch.stack(
        [
            torch.roll(torch.arange(5, dtype=torch.float32), state_index % 5)
            for state_index in range(37)
        ]
    )
    classes = {
        f"class{index}": {
            "signature_class_id": f"class{index}",
            "member_transition_ids": [f"t{index}"],
            "canonical_transition_id": f"t{index}",
        }
        for index in range(5)
    }
    manifest = build_bounded_a_pairs(
        labels_a=labels,
        scalar_utilities=utilities,
        scores=scores,
        ordered_state_ids=states,
        ordered_transition_ids=transitions,
        transition_token_counts={value: 10 for value in transitions},
        classes=classes,
        target_size=128,
        seed=23,
    )
    assert manifest["pair_count"] == 128
    assert manifest["task_count"] == 37
    assert all(manifest["coverage_checks"].values())


class _ToyProgramAdapter:
    def render_state(self, env_state, history):
        return f"{env_state}:{len(history)}"

    def extract_transition_memories(self, trajectory):
        return list(trajectory)

    def render_raw_memory_teacher(self, memory, prompt_profile):
        return f"{prompt_profile}:{memory}"

    def reference_target(self, example):
        return str(example)

    def evaluate_generated_action(
        self, response_text, code, target_action, observation, target_observation
    ):
        return {"match": code == target_action and observation == target_observation}


def test_toy_adapter_field_cycle_does_not_import_appworld(monkeypatch) -> None:
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "appworld" or name.startswith("appworld."):
            raise AssertionError("toy program path imported AppWorld")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    adapter = _ToyProgramAdapter()
    assert isinstance(adapter, ProgramBenchmarkAdapter)
    field = WeightedFactorizedTransitionField(2, 2, 3)
    field.add_fast("t", "p", 1.0, torch.ones(2), torch.ones(3), torch.ones(2, 3))
    assert field.read(torch.ones(2), torch.ones(2)).shape == (3,)
    field.remove_fast("t")
    assert torch.equal(field.V0, torch.zeros_like(field.V0))
