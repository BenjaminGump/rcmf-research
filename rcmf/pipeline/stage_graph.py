from __future__ import annotations

from rcmf.pipeline.contracts import StageSpec


SHARED_STAGES = (
    "S00_environment_manifest",
    "S01_authoritative_corpus",
    "S02_task_and_parent_splits",
    "S03_transition_records",
    "S04_selector_supervision",
    "S05_transition_representations",
    "S05B_joint_source_contract_preflight",
    "S06_cv_folds_and_sampling",
    "S07_initial_parameter_snapshots",
    "S08_two_arm_contract",
    "S09_runtime_preflight_and_approval",
)

THREE_DEMO_STAGES = (
    "D00_state_representations",
    "D01_selector_candidate_cv",
    "D02_selector_candidate_selection",
    "D03_final_selector_ensemble",
    "D04_selector_factorization",
    "D05_selected_memory_manifest",
    "D06_paired_causal_outcomes",
    "D07_policy_teacher",
    "D08_zero_cache_and_training_units",
    "D09_writer_reader_epoch_1",
    "D10_writer_reader_epoch_2",
    "D11_heldout_teacher_forced",
    "D12_heldout_one_step",
    "D13_heldout_full_trajectory",
    "D14_checkpoint_selection",
    "D15_401_memory_field",
    "D16_compile_and_add_98_memories",
    "D17_499_memory_deployment_field",
    "D18_common_one_demo_dev_bare",
    "D19_common_one_demo_dev_correct",
    "D20_common_one_demo_dev_shuffle",
    "D21_historical_reproduction_analysis",
    "D22_three_demo_reproduction_gate",
)

ONE_DEMO_STAGES = (
    "O00_state_representations",
    "O01_selector_candidate_cv",
    "O02_selector_candidate_selection",
    "O03_final_selector_ensemble",
    "O04_selector_factorization",
    "O05_selected_memory_manifest",
    "O06_paired_causal_outcomes",
    "O07_policy_teacher",
    "O08_zero_cache_and_training_units",
    "O09_writer_reader_epoch_1",
    "O10_writer_reader_epoch_2",
    "O11_heldout_teacher_forced",
    "O12_heldout_one_step",
    "O13_heldout_full_trajectory",
    "O14_checkpoint_selection",
    "O15_401_memory_field",
    "O16_compile_and_add_98_memories",
    "O17_499_memory_deployment_field",
    "O18_common_one_demo_dev_correct",
    "O19_common_one_demo_dev_shuffle",
)

FINAL_STAGES = (
    "F00_two_arm_paired_analysis",
    "F01_portability_validation",
    "F02_git_safe_audit_export",
    "F03_final_report_and_handoff",
)


def _stage(
    stage_id: str,
    arm: str,
    dependency: str | None,
    *,
    conditional_on: str | None = None,
) -> StageSpec:
    scientific = stage_id not in SHARED_STAGES
    gpu_tokens = (
        "representations",
        "candidate_cv",
        "ensemble",
        "causal_outcomes",
        "policy_teacher",
        "zero_cache",
        "writer_reader",
        "teacher_forced",
        "one_step",
        "full_trajectory",
        "dev_",
    )
    uses_gpu = any(token in stage_id for token in gpu_tokens)
    return StageSpec(
        stage_id=stage_id,
        arm=arm,
        dependencies=(dependency,) if dependency else (),
        command=("{python}", "scripts/run_rcmf_reproducible_stage_14b.py", "--stage", stage_id),
        validator="three_demo_reproduction_gate" if stage_id == "D22_three_demo_reproduction_gate" else "completion_manifest",
        scientific=scientific,
        uses_gpu=uses_gpu,
        conditional_on=conditional_on,
        expected_outputs=("output_manifest.json", "validator.json"),
    )


def build_exp037a_stage_graph() -> tuple[StageSpec, ...]:
    rows: list[StageSpec] = []
    previous: str | None = None
    for stage_id in SHARED_STAGES:
        rows.append(_stage(stage_id, "shared", previous))
        previous = stage_id
    for stage_id in THREE_DEMO_STAGES:
        rows.append(_stage(stage_id, "3d", previous))
        previous = stage_id
    one_demo_previous = "D22_three_demo_reproduction_gate"
    for stage_id in ONE_DEMO_STAGES:
        rows.append(
            _stage(
                stage_id,
                "1d",
                one_demo_previous,
                conditional_on="D22_three_demo_reproduction_gate",
            )
        )
        one_demo_previous = stage_id
    # Final reporting follows the gate when the conditional arm is skipped. When
    # the gate passes, topological order ensures the conditional arm finishes first.
    final_dependency = "D22_three_demo_reproduction_gate"
    for stage_id in FINAL_STAGES:
        rows.append(_stage(stage_id, "final", final_dependency))
        final_dependency = stage_id
    return tuple(rows)
