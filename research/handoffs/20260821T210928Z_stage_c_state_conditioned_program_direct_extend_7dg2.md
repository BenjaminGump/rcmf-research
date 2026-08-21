# EXP-025D-G2 Structured Handoff

## Status

- Run: `state_conditioned_program_direct_extend_7dg2_20260821_001`
- Branch: `research/v4-direct-behavior-program`
- Starting SHA: `d66733e1a3c47a405a5781c613965b55f8078af2`
- GPU training-source SHA: `673e4e3244651b657dee8ded117a5697b73349c7`
- Final analysis-source SHA: `f8dcc3695a7cc22f9cb3cf67a16f77a977e992c0`
- Seed: `25101`
- Final branch: `calibrated_factorized_program_not_behaviorally_retained`
- Safe scientific stop: yes

## What Ran

The exact EXP-025D-Direct r16 u16 model, private decoder, Adam state, RNG, 479
pair order, and 16 update counts were restored. A-only continuation visited
u32 and u48, then stopped under the preregistered rule; u64 was not visited.
The u48 checkpoint and `gamma=1.0` were frozen on A-validation. B/C/D/E were
then evaluated once. Because the teacher-forced gate passed, 180 H1-H4 frozen-
Qwen/AppWorld same-world conditions ran automatically.

## Key Results

- A u16/u32/u48 Spearman: `0.40777 / 0.59360 / 0.61472`
- A u16/u32/u48 Huber: `0.24820 / 0.15580 / 0.15014`
- B/E Spearman: `0.55158 / 0.57384`
- B/E Huber reduction versus zero: `29.70% / 30.64%`
- B/E correct Huber beats transition shuffle and memory swap
- Teacher-forced gate: passed
- Primary H1 action-signature/successor/execution:
  `0.28125 / 0.53125 / 0.93750`
- Primary C0 action-signature/successor/execution:
  `0.31250 / 0.43750 / 0.93750`
- Primary F3 action-signature/successor/execution:
  `0.68750 / 0.78125 / 1.00000`
- H1 successor oracle retention: `0.27273`
- H1 action-signature oracle retention: `-0.08333`
- H1 positive tasks: `3/9`
- One-step gate: failed

All final bootstrap intervals were recomputed using only global seed `25101`.
The earlier seed-offset analysis remains preserved under deprecated provenance
filenames and did not affect generations, row metrics, or the decision.

## Recovery History

Ten attempts are closed in the append-only ledger. `train-001` exposed the
atomic-row-directory loader bug and `train-002` exposed CUDA RNG restoration
through a non-CPU tensor; both stopped before an update and both repairs have
tests. `train-003` resumed exact state and completed. The final two attempts
are an analysis-only single-seed correction and finalizer.

## Immutable Artifacts

- Root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/state_conditioned_program_direct_extend_7dg2_20260821_001`
- Ledger: `attempts.jsonl`
- Resume integrity: `resume_integrity.json`
- Runtime preflight: `runtime_preflight.json`
- Calibration: `calibration_audit_u16.json`
- Checkpoints: `factorized/checkpoints/model_u32.pt` and `model_u48.pt`
- Selection: `factorized/selection_summary.json`
- Teacher-forced: `teacher_forced_summary.json` and `teacher_forced_report.md`
- One-step: `one_step/generation_summary.json`, `one_step/analysis.json`, and
  `one_step/one_step_report.md`
- Final: `final_exp025dg2_summary.json` and `final_exp025dg2_report.md`

## Interpretation And Next Action

VERIFIED: convergence/calibration repaired teacher-forced generalization, but
the compiled intervention did not retain the raw transition's one-step benefit
and did not beat shuffled transition.

INFERENCE: scalar utility fit is insufficient to preserve the structured
behavioral mechanism of raw transition content.

UNVERIFIED: full-bank compilation and end-to-end RCMF.

Do not resume r16, start r64, or begin full-bank integration. Freeze the clean
selector/raw-transition causal result for the deadline path. Any new compiled-
program experiment needs a separately approved behavioral-retention contract.

