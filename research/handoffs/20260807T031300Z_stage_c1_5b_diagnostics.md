# Handoff: Stage C1 5B Corrected LOO Diagnostics

Date UTC: 2026-08-07T03:13:00Z

## Scope

Milestone 5B completed. This was checkpoint-only evaluation and diagnostics.

Not run:

- Stage-C1 retraining
- Stage C2
- selector/program/injector/Qwen fine-tuning
- teacher response cache regeneration
- AppWorld generation/evaluation

## Source

- Branch: `workflow/research-loop`
- Source commit: `f998a45e2889802d0ba06dd00757461b1ebf16c5`
- Lambda project path: `/lambda/nfs/rcmf-persist/project`
- Lambda venv: `/home/ubuntu/venvs/rcmf-py311`

## Command

```bash
python scripts/run_stage_c1_5b_diagnostics.py \
  --config configs/benchmark/stage_c1_signed_program.yaml \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
  --teacher-cache-dir runs/teacher/raw_text_full_cache_20260805_001 \
  --labels-dir runs/stage_b/student_labels_20260806_002 \
  --response-cache-dir runs/stage_c1/response_cache_20260806_001 \
  --signed-field-dir runs/stage_b/signed_field_4c_20260806_002 \
  --representation-cache-dir runs/experiments/appworld_qwen_repr_full_prompt_filtered_no_2a163ab3_20260803_101000/train/representation_cache \
  --stage-c1-dir runs/stage_c1/signed_program_c1_20260806_002 \
  --output-dir runs/stage_c1/stage_c1_5b_diagnostics_20260807_001 \
  --seeds 1 2 3 \
  --all-memory-subset-size 32 \
  --aggregate-read-subset-size 16 \
  --device cuda
```

## Artifacts

- Main artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/stage_c1_5b_diagnostics_20260807_001`
- Summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/stage_c1_5b_diagnostics_20260807_001/summary.json`
- Report:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/stage_c1_5b_diagnostics_20260807_001/report.md`
- Corrected LOO:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/stage_c1_5b_diagnostics_20260807_001/corrected_leave_one_out.json`
- Contribution analysis:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/stage_c1_5b_diagnostics_20260807_001/contribution_analysis.json`
- Free-ID comparison:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/stage_c1_5b_diagnostics_20260807_001/free_id_comparison.json`
- Injector scale sweep:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/stage_c1_5b_diagnostics_20260807_001/injector_scale_sweep.json`
- Run log:
  `/lambda/nfs/rcmf-persist/runs/logs/stage_c1_5b_diagnostics_20260807_001.log`

## Verification

- Local full tests: `80 passed`.
- Lambda Stage-C1 tests: `11 passed`.
- Response cache validation: passed, 638 states, 0 errors.
- Runtime: `5757.08` seconds.
- Old leave-one-out result: invalid and superseded.

## Findings

VERIFIED:

- The old LOO bug was real: validation full-bank mask construction ignored
  mutations to `legal_effective_mask`.
- Explicit counterfactual include-mask override now removes memory deltas.
- Corrected teacher-best LOO mean effect is nonzero but small:
  `0.002334`, CI `[0.000444, 0.004588]`.
- Selector-top memory has larger compiled effect than teacher-best:
  teacher-best minus selector-top mean `-0.004244`, CI
  `[-0.008464, -0.000886]`.
- Raw-teacher-best selector Recall@1/4/8:
  `0.113043/0.313043/0.466667`.
- Teacher-best median signed-score rank is `10/36`; p75 rank is `20`.
- `24.35%` of teacher-best memories receive negative signed score.
- Teacher utility does not correlate with analytic or compiled behavioral
  effect in the current Stage-C1 field.
- Content is statistically worse than free-ID on target NLL overall and on
  positive-teacher states, but not on sparse KL or no-positive states.
- Scale `0.25` fixes no-positive degradation but has much worse target NLL
  than scale `1.0`.

INFERENCES:

- The exact-zero LOO claim should be retracted.
- The Stage-C1 gate remains failed.
- The next bottleneck is selector-teacher alignment more than the aggregate
  read rule.

UNVERIFIED:

- Whether a selector retrained with teacher-best/top-utility supervision can
  retain Stage-4C ranking gains.
- Whether restrained-injector retraining helps after selector alignment is
  repaired.

## Decision

- Branch: `selector_teacher_alignment_issue`.
- Recommendation: repair selector-teacher alignment before another
  program-channel run.
- Stage C2 remains blocked.

## Next Step

Run an alignment-focused Stage-B/selector repair:

- report raw-teacher-best Recall@1/4/8;
- report utility mass coverage;
- prevent strong-positive teacher memories from receiving negative signed
  score;
- rerun 5B corrected LOO diagnostics before any Stage-C program retraining.

## Safe Status

- tmux: no server running.
- GPU: `0 MiB / 0%`.
- Safe to terminate Lambda.
